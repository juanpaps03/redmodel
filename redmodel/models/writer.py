"""
    Copyright (C) 2011 Maximiliano Pin

    Redmodel is free software: you can redistribute it and/or modify
    it under the terms of the GNU Lesser General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Redmodel is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Lesser General Public License for more details.

    You should have received a copy of the GNU Lesser General Public License
    along with Redmodel.  If not, see <http://www.gnu.org/licenses/>.
"""

from redmodel.containers import ListHandle, SetHandle, SortedSetHandle, ContainerWriter, ListWriter, SetWriter, SortedSetWriter
from redmodel.models.base import Handle, Model
from redmodel.models.attributes import ListField, SetField, SortedSetField
from redmodel.models.exceptions import UniqueError, NotFoundError
from redmodel import connection as ds

class ModelWriter(object):
    def __init__(self, model):
        self.model = model
        self.modname = model.__name__

    def __check_unique(self, fld, val):
        #TODO watch (optimistic lock) to allow multithread?
        k = 'u:{0}:{1}'.format(self.modname, fld)
        if ds.hexists(k, val):
            raise UniqueError(k, val)

    def __index(self, pl, oid, fld, val, unique):
        if unique:
            k = 'u:{0}:{1}'.format(self.modname, fld)
            pl.hset(k, val, oid)
        else:
            k = 'i:{0}:{1}:{2}'.format(self.modname, fld, val)
            pl.sadd(k, oid)

    def __unindex(self, pl, oid, fld, val, unique):
        if unique:
            k = 'u:{0}:{1}'.format(self.modname, fld)
            pl.hdel(k, val)
        else:
            k = 'i:{0}:{1}:{2}'.format(self.modname, fld, val)
            pl.srem(k, oid)

    def __zindex(self, pl, oid, fld, val):
        k = 'z:{0}:{1}'.format(self.modname, fld)
        pl.zadd(k, **{oid: val})

    def __zunindex(self, pl, oid, fld):
        k = 'z:{0}:{1}'.format(self.modname, fld)
        pl.zrem(k, oid)

    def __list(self, pl, oid, fld, val):
        k = 'l:{0}:{1}:{2}'.format(self.modname, fld, val)
        pl.rpush(k, oid)

    def __unlist(self, pl, oid, fld, val):
        k = 'l:{0}:{1}:{2}'.format(self.modname, fld, val)
        pl.lrem(k, oid)

    def __unindex_all(self, pl, obj):
        for a in obj._attributes:
            if a.indexed or a.zindexed or a.listed:
                fld = a.name
                v = obj._indexed_values[fld]
                if v is not None:
                    if a.indexed:
                        self.__unindex(pl, obj.oid, fld, v, a.unique)
                    if a.zindexed:
                        self.__zunindex(pl, obj.oid, fld)
                    if a.listed:
                        self.__unlist(pl, obj.oid, fld, v)

    def __update_attrs(self, obj, data):
        if (len(data)):
            self._check_unique_for_update(obj, data)
            pl = ds.pipeline(True)
            self._do_update_attrs(pl, obj, data)
            pl.execute()

    def _check_unique_for_update(self, obj, data):
        attr_dict = self.model._attr_dict
        for fld in data.iterkeys():
            a = attr_dict[fld]
            if a.unique:
                v = data[fld]
                oldv = obj._indexed_values[fld]
                if v != oldv:
                    self.__check_unique(fld, v)

    def _do_update_attrs(self, pl, obj, data):
        attr_dict = self.model._attr_dict
        pl.hmset(obj.key, data)
        for fld in data.iterkeys():
            a = attr_dict[fld]
            if a.indexed or a.zindexed or a.listed:
                v = data[fld]
                oldv = obj._indexed_values[fld]
                if a.indexed:
                    if oldv is not None:
                        self.__unindex(pl, obj.oid, fld, oldv, a.unique)
                    self.__index(pl, obj.oid, fld, v, a.unique)
                if a.zindexed:
                    if oldv is not None:
                        self.__zunindex(pl, obj.oid, fld)
                    self.__zindex(pl, obj.oid, fld, v)
                if a.listed:
                    if oldv is not None:
                        self.__unlist(pl, obj.oid, fld, oldv)
                    self.__list(pl, obj.oid, fld, v)
                obj._indexed_values[fld] = v

    def create(self, obj, owner = None):
        assert type(obj) is self.model and obj.oid is None
        assert owner is None or owner.oid is not None
        assert (owner is None and self.model._owner is None) or (type(owner) is self.model._owner) or (type(owner) is Handle and owner.model is self.model._owner), 'Wrong owner.'
        if owner is None:
            obj.oid = str(ds.incr(self.modname + ':id'))
        else:
            obj.oid = owner.oid
        self.__update_attrs(obj, obj.make_dict())
        key = obj.key
        for l in obj._lists:
            obj.__dict__[l.name] = ListHandle(key + ':' + l.name, l.target_type)
        for s in obj._sets:
            obj.__dict__[s.name] = SetHandle(key + ':' + s.name, s.target_type)
        for z in obj._zsets:
            obj.__dict__[z.name] = SortedSetHandle(key + ':' + z.name, z.target_type)

    def _get_update_data(self, obj, **kwargs):
        assert type(obj) is self.model and obj.oid is not None
        assert len(kwargs) > 0
        return obj.update_attributes_dict(**kwargs)

    def update(self, obj, **kwargs):
        data = self._get_update_data(obj, **kwargs)
        self.__update_attrs(obj, data)

    def update_all(self, obj):
        assert type(obj) is self.model and obj.oid is not None
        self.__update_attrs(obj, obj.make_dict())

    def delete(self, obj):
        assert type(obj) is self.model and obj.oid is not None
        if not ds.exists(obj.key):
            raise NotFoundError(obj.key)
        pl = ds.pipeline(True)
        self.__unindex_all(pl, obj)
        pl.delete(obj.key)
        pl.execute()
        obj.oid = None

class ContainerFieldWriter(ContainerWriter):
    def __init__(self, field, element_writer = None):
        assert (not field.owned and element_writer is None) or (field.owned and element_writer is not None)
        self.field = field
        self.element_writer = element_writer
        index_key = None
        if field.indexed:
            index_key = 'u:' if field.unique else 'i:'
            index_key += field.model.__name__ + ':' + field.name
        ContainerWriter.__init__(self, field.target_type, index_key, field.unique)

    def append(self, hcont, value, score = None):
        if self.field.owned:
            assert value.oid is None
            self.element_writer.create(value)
            assert value.oid is not None
            value = value.handle()
        ContainerWriter.append(self, hcont, value, score)

    def remove(self, hcont, value):
        assert (not self.field.owned) or isinstance(value, Model)
        removed = ContainerWriter.remove(self, hcont, value)
        if self.field.owned:
            if not removed:
                raise NotFoundError('{0} in {1}'.format(value.handle(), hcont))
            assert value.oid is not None
            self.element_writer.delete(value)
            assert value.oid is None

class ListFieldWriter(ContainerFieldWriter, ListWriter):
    def __init__(self, field, element_writer = None):
        assert type(field) is ListField
        ContainerFieldWriter.__init__(self, field, element_writer)

class SetFieldWriter(ContainerFieldWriter, SetWriter):
    def __init__(self, field, element_writer = None):
        assert type(field) is SetField
        ContainerFieldWriter.__init__(self, field, element_writer)

class SortedSetFieldWriter(ContainerFieldWriter, SortedSetWriter):
    def __init__(self, field, element_writer = None):
        assert type(field) is SortedSetField
        ContainerFieldWriter.__init__(self, field, element_writer)

    def append(self, hcont, value, score = None):
        """ If sort_field is specified, score must be None.
            If sort_field is not specified, score is mandatory. """
        assert (score is None) != (self.field.sort_field is None)
        if score is None:
            score = getattr(value, self.field.sort_field.name)
        ContainerFieldWriter.append(self, hcont, value, score)

    def update(self, hcont, obj, **kwargs):
        assert self.field.owned
        data = self.element_writer._get_update_data(obj, **kwargs)
        self.element_writer._check_unique_for_update(obj, data)
        pl = ds.pipeline(True)
        SortedSetWriter.raw_remove(self, pl, hcont, obj.oid)
        self.element_writer._do_update_attrs(pl, obj, data)
        score = getattr(obj, self.field.sort_field.name)
        SortedSetWriter.raw_append(self, pl, hcont, obj.oid, score)
        pl.execute()

    def update_all(self, hcont, obj):
        assert self.field.owned
        data = obj.make_dict()
        self.element_writer._check_unique_for_update(obj, data)
        pl = ds.pipeline(True)
        SortedSetWriter.raw_remove(self, pl, hcont, obj.oid)
        self.element_writer._do_update_attrs(pl, obj, data)
        score = getattr(obj, self.field.sort_field.name)
        SortedSetWriter.raw_append(self, pl, hcont, obj.oid, score)
        pl.execute()
