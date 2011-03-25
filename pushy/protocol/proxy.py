# Copyright (c) 2008, 2009 Andrew Wilkins <axwalk@gmail.com>
# 
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use,
# copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following
# conditions:
# 
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.

from pushy.protocol.message import message_types
import pushy.util
import exceptions, types


class ProxyType(object):
    def __init__(self, code, name):
        self.code = code
        self.name = name
    def __repr__(self):
        return "ProxyType(%d, '%s')" % (self.code, self.name)
    def __str__(self):
        return self.name
    def __int__(self):
        return self.code
    def __eq__(self, other):
        if type(other) is int: return other == self.code
        elif type(other) is str: return other == self.name
        return other.__class__ is ProxyType and other.code == self.code

    @staticmethod
    def get(obj):
        # First check if it's a class
        class_ = type(obj)
        if hasattr(class_, "pushy_proxy_type"):
            return class_.pushy_proxy_type

        if isinstance(obj, Exception):
            return ProxyType.exception
        if isinstance(obj, dict):
            return ProxyType.dictionary
        if isinstance(obj, list):
            return ProxyType.list
        if isinstance(obj, set):
            return ProxyType.list
        if isinstance(obj, types.ModuleType):
            return ProxyType.module
        if isinstance(obj, types.ClassType):
            return ProxyType.oldstyleclass
        return ProxyType.object

    @staticmethod
    def getargs(proxy_type, obj):
        if type(proxy_type) is int:
            proxy_type = proxy_types[proxy_type]
        if proxy_type is ProxyType.exception:
            module_ = obj.__class__.__module__
            if module_ == "exceptions":
                return obj.__class__.__name__
        elif proxy_type is ProxyType.dictionary:
            args = None
            if len(obj) > 0:
                args = tuple(obj.items())
            return args
        elif proxy_type in (ProxyType.list, ProxyType.set):
            return tuple(obj)
        return None

    @staticmethod
    def getoperators(obj):
        class_ = type(obj)
        if hasattr(class_, "pushy_operator_mask"):
            return class_.pushy_operator_mask
        else:
            mask = sum((
                (1 << t.code) for t in message_types \
                if t.name.startswith("op__") and hasattr(obj, t.name[2:])
            ))
            return mask


proxy_names = (
  "oldstyleclass",
  "object",
  "exception",
  "dictionary",
  "list",
  "set",
  "module"
)
proxy_types = []
for i,t in enumerate(proxy_names):
    p = ProxyType(i, t)
    proxy_types.append(p)
    setattr(ProxyType, t, p)


###############################################################################


def create_instance(args, conn, on_proxy_init):
    class ClassObjectProxy:
        def __init__(self):
            on_proxy_init(self)
        def __call__(self, *args, **kwargs):
            message_type = pushy.protocol.message.MessageType.op__call__
            return conn.operator(message_type, self, args, kwargs)
        def __getattr__(self, name):
            if name == "__call__":
                raise AttributeError, "__call__"
            return conn.getattr(self, name)
    return ClassObjectProxy


def create_object(args, conn, on_proxy_init):
    class ObjectProxy(object):
        def __init__(self):
            on_proxy_init(self)
            object.__init__(self)
        def __getattribute__(self, name):
            return conn.getattr(self, name)
    return ObjectProxy


def create_exception(args, conn, on_proxy_init):
    BaseException = Exception
    if args is not None:
        BaseException = getattr(exceptions, args)
    class ExceptionProxy(BaseException):
        def __init__(self):
            on_proxy_init(self)
            BaseException.__init__(self)
        def __getattribute__(self, name):
            return conn.getattr(self, name)
    return ExceptionProxy


def create_dictionary(args, conn, on_proxy_init):
    class DictionaryProxy(dict):
        def __init__(self):
            on_proxy_init(self)
            if args is not None:
                dict.__init__(self, args)
            else:
                dict.__init__(self)
        def __getattribute__(self, name):
            return conn.getattr(self, name)
    return DictionaryProxy


def create_list(args, conn, on_proxy_init):
    class ListProxy(list):
        def __init__(self):
            on_proxy_init(self)
            list.__init__(self, args)
        def __getattribute__(self, name):
            return conn.getattr(self, name)
    return ListProxy


def create_set(args, conn, on_proxy_init):
    class SetProxy(set):
        def __init__(self):
            on_proxy_init(self)
            set.__init__(self, args)
        def __getattribute__(self, name):
            return conn.getattr(self, name)
    return SetProxy


def create_module(args, conn, on_proxy_init):
    class ModuleProxy(types.ModuleType):
        def __init__(self):
            on_proxy_init(self)
            types.ModuleType.__init__(self, "")
            self.__importer = None
        def __getattribute__(self, name):
            try:
                return conn.getattr(self, name)
            except AttributeError:
                if self.__importer:
                    return self.__importer(self.__name__ + "." + name)
                else:
                    raise
    return ModuleProxy


class_factories = {
    ProxyType.oldstyleclass: create_instance,
    ProxyType.object:        create_object,
    ProxyType.exception:     create_exception,
    ProxyType.dictionary:    create_dictionary,
    ProxyType.list:          create_list,
    ProxyType.set:           create_set,
    ProxyType.module:        create_module
}

def Proxy(opmask, proxy_type, args, conn, on_proxy_init):
    """
    Create a proxy object, which delegates attribute access and method
    invocation to an object in a remote Python interpreter.
    """

    # Determine the class to use for the proxy type.
    proxy_type = proxy_types[proxy_type]
    ProxyClass = class_factories[proxy_type](args, conn, on_proxy_init)

    # Store the operator mask and proxy type in the class. This will be used by
    # ProxyType.getoperators() and ProxyType.get()
    #
    # XXX Important! Without this, the performance of tunnelled connections is
    #                unusably slow due to the chatter induced by many calls to
    #                hasattr/getattr.
    ProxyClass.pushy_operator_mask = opmask
    ProxyClass.pushy_proxy_type = proxy_type

    # Creates a lambda with the operator type and connection bound.
    def bound_operator(type_):
        return lambda self, *args, **kwargs: \
            (conn.operator(type_, self, args, kwargs))

    # Create proxy operators.
    types = (t for t in message_types if (opmask & (1 << t.code)))
    map(lambda t: setattr(ProxyClass, t.name[2:], bound_operator(t)), types)

    # Add other standard methods.
    setattr(ProxyClass, "__str__", lambda self: conn.getstr(self))
    setattr(ProxyClass, "__repr__", lambda self: conn.getrepr(self))
    setattr(ProxyClass, "__setattr__", lambda *args: conn.setattr(*args))

    # Make sure we support the iteration protocol properly
    #if hasattr(ProxyClass, "__iter__"):
    setattr(ProxyClass, "next", lambda self: conn.getattr(self, "next")())

    return ProxyClass()

