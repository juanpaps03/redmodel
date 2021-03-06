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

from redmodel import connection as ds
from redmodel.containers import ListHandle, SetHandle, SortedSetHandle
from redmodel.models.attributes import Attribute, ReferenceField, ListField, SetField, SortedSetField, Recursive
from redmodel.models.exceptions import NotFoundError, BadArgsError

class Handle(object):
    def __init__(self, model, oid):
        self.model = model
        self.oid = str(oid) if oid else '0'

    def __repr__(self):
        return '<{0}: {1}>'.format(self.__class__.__name__, self.key)

    def __eq__(self, other):
        return self.model == other.model and self.oid == other.oid

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.oid)

    def __nonzero__(self):
        return bool(int(self.oid))

    @property
    def key(self):
        return self.model.key_by_id(self.oid)

    def load(self):
        d = ds.hgetall(self.key)
        obj = self.model()
        obj.oid = self.oid
        obj._indexed_values = {}
        try:
            for a in self.model._attributes:
                v = d[a.name]
                obj.__dict__[a.name] = a.typecast_for_read(v)
                if a.indexed or a.zindexed or a.listed:
                    obj._indexed_values[a.name] = v
            for l in self.model._lists:
                obj.__dict__[l.name] = ListHandle(self.key + ':' + l.name,
                                                  l.target_type)
            for s in self.model._sets:
                obj.__dict__[s.name] = SetHandle(self.key + ':' + s.name,
                                                 s.target_type)
            for z in self.model._zsets:
                obj.__dict__[z.name] = SortedSetHandle(self.key + ':' + z.name,
                                                       z.target_type)
            return obj
        except KeyError:
            if len(d) == 0 and not self.model.exists(self.oid):
                raise NotFoundError(self.key)
            else:
                raise

def ishandle(obj, model):
    return isinstance(obj, Handle) and obj.model is model

class ModelMeta(type):
    def __new__(cls, name, bases, attrs):
        attr_dict = {}
        attributes = []
        lists = []
        sets = []
        zsets = []
        attrs['_owner'] = None
        attrs['_attr_dict'] = attr_dict
        attrs['_attributes'] = attributes
        attrs['_lists'] = lists
        attrs['_sets'] = sets
        attrs['_zsets'] = zsets
        new_type = type.__new__(cls, name, bases, attrs)
        for k, v in attrs.iteritems():
            if k == 'owner':
                assert issubclass(v, Model)
                new_type._owner = v
            elif isinstance(v, Attribute):
                v.name = k
                if v.zindexed:
                    zkey = 'z:{0}:{1}'.format(name, k)
                    v.zindex = SortedSetHandle(zkey, new_type)
                attr_dict[k] = v
                attributes.append(v)
            elif isinstance(v, ListField):
                v.name = k
                v.model = new_type
                if v.target_type == Recursive:
                    v.target_type = new_type
                lists.append(v)
            elif isinstance(v, SetField):
                v.name = k
                v.model = new_type
                if v.target_type == Recursive:
                    v.target_type = new_type
                sets.append(v)
            elif isinstance(v, SortedSetField):
                v.name = k
                v.model = new_type
                if v.target_type == Recursive:
                    v.target_type = new_type
                zsets.append(v)
        return new_type

class Model(object):
    __metaclass__ = ModelMeta

    oid = None

    @classmethod
    def by_id(cls, oid):
        return Handle(cls, oid)

    @classmethod
    def by_owner(cls, owner):
        assert type(owner) is cls._owner or (type(owner) is Handle and owner.model is cls._owner)
        return Handle(cls, owner.oid)

    @classmethod
    def key_by_id(cls, oid):
        return cls.__name__ + ':' + str(oid)

    @classmethod
    def exists(cls, oid):
        return ds.exists(cls.key_by_id(oid))

    @classmethod
    def find(cls, **kwargs):
        assert len(kwargs) == 1
        fldcond = kwargs.keys()[0].split('__')
        fld = fldcond[0]
        val = kwargs.values()[0]
        f = cls.__dict__[fld]
        if isinstance(val, Handle):
            assert not hasattr(f, 'target_type') or val.model is f.target_type
            val = val.oid
        else:
            assert not hasattr(f, 'target_type') or type(val) is f.target_type
            if isinstance(val, Model):
                val = val.oid
        if len(fldcond) == 1:
            k = 'u:{0}:{1}'.format(cls.__name__, fld)
            return Handle(cls, ds.hget(k, val))
        else:
            cond = fldcond[1]
            if cond == 'contains':
                k = 'u:{0}:{1}'.format(cls.__name__, fld)
                return Handle(cls, ds.hget(k, val))

    @classmethod
    def multifind(cls, **kwargs):
        assert len(kwargs) == 1
        fldcond = kwargs.keys()[0].split('__')
        fld = fldcond[0]
        val = kwargs.values()[0]
        f = cls.__dict__[fld]
        if isinstance(val, Handle):
            assert not hasattr(f, 'target_type') or val.model is f.target_type
            val = val.oid
        else:
            assert not hasattr(f, 'target_type') or type(val) is f.target_type
            if isinstance(val, Model):
                val = val.oid
        if len(fldcond) == 1:
            k = 'i:{0}:{1}:{2}'.format(cls.__name__, fld, val)
            return set(map(lambda m: Handle(cls, m), ds.smembers(k)))
        else:
            cond = fldcond[1]
            if cond == 'contains':
                k = 'i:{0}:{1}:{2}'.format(cls.__name__, fld, val)
                return set(map(lambda m: Handle(cls, m), ds.smembers(k)))

    @classmethod
    def zfind(cls, **kwargs):
        """ Calls typecast_for_write, so it can be used with datetime values,
            or other special field types. For other z* methods,
            typecast_for_write must be called explicitly if needed. """
        assert len(kwargs) == 1
        fldcond = kwargs.keys()[0].split('__')
        fld = fldcond[0]
        f = cls.__dict__[fld]
        val = kwargs.values()[0]
        if isinstance(val, tuple):
            assert len(val) == 2
            val = (f.typecast_for_write(val[0]), f.typecast_for_write(val[1]))
        else:
            val = f.typecast_for_write(val)
        if len(fldcond) == 1:
            return f.zindex.zfind(eq = val)
        else:
            cond = fldcond[1]
            return f.zindex.zfind(**{cond: val})

    @classmethod
    def getlist(cls, start_ = 0, end_ = -1, **kwargs):
        assert len(kwargs) == 1
        fld, val = kwargs.popitem()
        f = cls.__dict__[fld]
        if isinstance(val, Handle):
            assert not hasattr(f, 'target_type') or val.model is f.target_type
            val = val.oid
        else:
            assert not hasattr(f, 'target_type') or type(val) is f.target_type
            if isinstance(val, Model):
                val = val.oid
        k = 'l:{0}:{1}:{2}'.format(cls.__name__, fld, val)
        return map(lambda m: Handle(cls, m), ds.lrange(k, start_, end_))

    @classmethod
    def zrange(cls, fld, start = 0, end = -1):
        return cls._zindex(fld).zrange(start, end)

    @classmethod
    def zrevrange(cls, fld, start = 0, end = -1):
        return cls._zindex(fld).zrevrange(start, end)

    @classmethod
    def zrangebyscore(cls, fld, smin, smax, start = None, num = None):
        return cls._zindex(fld).zrangebyscore(smin, smax, start, num)

    @classmethod
    def zrevrangebyscore(cls, fld, smax, smin, start = None, num = None):
        return cls._zindex(fld).zrevrangebyscore(smax, smin, start, num)

    @classmethod
    def zcount(cls, fld, smin, smax):
        return cls._zindex(fld).zcount(smin, smax)

    @classmethod
    def zrank(cls, fld, obj):
        return cls._zindex(fld).zrank(obj)

    @classmethod
    def zrevrank(cls, fld, obj):
        return cls._zindex(fld).zrevrank(obj)

    def __new__(cls, *args, **kwargs):
        if len(args) == 0:
            if len(kwargs) == 0:
                return super(Model, cls).__new__(cls)
            atnames = set([a.name for a in cls._attributes])
            if len(set(kwargs.keys()).symmetric_difference(atnames)) != 0:
                raise BadArgsError(str(sorted(atnames)) + ' expected, not ' + str(sorted(kwargs.keys())))
            obj = super(Model, cls).__new__(cls)
            obj.update_attributes(**kwargs)
            obj._indexed_values = {}
            for a in obj._attributes:
                if a.indexed or a.zindexed or a.listed:
                    obj._indexed_values[a.name] = None
            for l in obj._lists:
                obj.__dict__[l.name] = None
            for s in obj._sets:
                obj.__dict__[s.name] = None
            return obj
        else:
            h = args[0]
            assert isinstance(h, Handle), type(h)
            assert h.model is cls, 'Expected ' + str(cls.__name__) + ' handle, not ' + str(h.model.__name__)
            return h.load()

    def __repr__(self):
        d = self.make_dict()
        return '<{0} {1}>'.format(self.key, str(d))

    @property
    def key(self):
        return self.key_by_id(self.oid)

    def handle(self):
        return Handle(self.__class__, self.oid)

    def update_attributes(self, **kwargs):
        for k, v in kwargs.iteritems():
            a = self._attr_dict[k]
            if isinstance(a, ReferenceField):
                if isinstance(v, Model):
                    v = v.handle()
                if isinstance(v, Handle):
                    assert v.model is a.target_type
                else:
                    v = a.target_type.by_id(v)
            self.__dict__[a.name] = v

    def update_attributes_dict(self, **kwargs):
        d = {}
        for k, v in kwargs.iteritems():
            a = self._attr_dict[k]
            if isinstance(a, ReferenceField):
                if isinstance(v, Model):
                    v = v.handle()
                if isinstance(v, Handle):
                    assert v.model is a.target_type
                else:
                    v = a.target_type.by_id(v)
            self.__dict__[a.name] = v
            d[k] = a.typecast_for_write(v)
        return d

    def make_dict(self):
        d = {}
        for a in self._attributes:
            if self.__dict__.has_key(a.name):
                d[a.name] = a.typecast_for_write(self.__dict__[a.name])
        return d

    @classmethod
    def _zindex(cls, fld):
        return cls.__dict__[fld].zindex
