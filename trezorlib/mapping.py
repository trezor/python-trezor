# This file is part of the TREZOR project.
#
# Copyright (C) 2012-2016 Marek Palatinus <slush@satoshilabs.com>
# Copyright (C) 2012-2016 Pavol Rusnak <stick@satoshilabs.com>
# Copyright (C) 2016      Jochen Hoenicke <hoenicke@gmail.com>
#
# This library is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this library.  If not, see <http://www.gnu.org/licenses/>.

import binascii
import re
from typing import Dict, Any, Iterable, Callable, List, Optional, Union, Tuple, NewType, Type, TypeVar

from . import messages
from . import protobuf

map_type_to_class = {}
map_class_to_type = {}


def build_map():
    for msg_name in dir(messages.MessageType):
        if msg_name.startswith('__'):
            continue

        try:
            msg_class = getattr(messages, msg_name)
        except AttributeError:
            raise ValueError("Implementation of protobuf message '%s' is missing" % msg_name)

        if msg_class.MESSAGE_WIRE_TYPE != getattr(messages.MessageType, msg_name):
            raise ValueError("Inconsistent wire type and MessageType record for '%s'" % msg_class)

        register_message(msg_class)


def register_message(msg_class):
    if msg_class.MESSAGE_WIRE_TYPE in map_type_to_class:
        raise Exception("Message for wire type %s is already registered by %s" %
                        (msg_class.MESSAGE_WIRE_TYPE, get_class(msg_class.MESSAGE_WIRE_TYPE)))

    map_class_to_type[msg_class] = msg_class.MESSAGE_WIRE_TYPE
    map_type_to_class[msg_class.MESSAGE_WIRE_TYPE] = msg_class


def get_type(msg):
    return map_class_to_type[msg.__class__]


def get_class(t):
    return map_type_to_class[t]


def _get_hint(name, hints):
    return hints.get(name, name)


class AdapterError(TypeError):
    pass


AdapterFunction = Callable[[Dict], Any]
AdapterDefinition = Union[
    None,
    str,
    AdapterFunction,
    'AdapterDict',
    List['AdapterDict'],
    List[AdapterFunction],
    Tuple[str, 'AdapterDict'],
    Tuple[str, List['AdapterDict']],
    Tuple[str, List[AdapterFunction]],
]
AdapterDict = Dict[str, 'AdapterDefinition']
AdapterType = TypeVar('AdapterType', bound=protobuf.MessageType)


class ProtoAdapter:

    BYTES_NOCONVERSION = 0
    BYTES_HEX = 1
    BYTES_BASE64 = 2

    def __init__(self, message_type: AdapterType, hints: AdapterDict=None,
                 select_field: str=None,
                 camel_case: bool=True,
                 bytes_conversion: int=None) -> None:
        self.message_type = message_type
        self.camel_case = camel_case
        self.bytes_conversion = bytes_conversion or self.BYTES_HEX
        self.select_field = select_field
        self.processors = self.preprocess_hints(hints or {})

    def find_with_case(self, string: str, keys: Iterable) -> str:
        if string in keys:
            return string
        if self.camel_case:
            camelcase_re = re.compile(r'([a-z0-9])([A-Z])')
            for key in keys:
                converted_key = camelcase_re.sub(r'\1_\2', key).lower()
                if converted_key == string:
                    return key
        return None

    def traverse_path(self, pathstr: str, data: Dict) -> Any:
        path = pathstr.split('.')
        root = data
        for step in path:
            key = self.find_with_case(step, root.keys())
            if key is None:
                return None
            root = root[key]
        return root

    def read_field(self, path: str) -> AdapterFunction:
        def reader(data: Dict) -> Any:
            return self.traverse_path(path, data)
        return reader

    def map_action(self, path: str, action: AdapterFunction) -> AdapterFunction:
        def reader(data: Dict) -> List[Any]:
            iterable = self.traverse_path(path, data)
            if iterable is None:
                return []
            else:
                return list(map(action, iterable))
        return reader

    def dict_to_adapter(self, name: str, typeinfo: protobuf.MessageType, hints: AdapterDict) -> AdapterFunction:
        return ProtoAdapter(typeinfo, hints, select_field=name,
                            camel_case=self.camel_case,
                            bytes_conversion=self.bytes_conversion)

    def preprocess_hints(self, hints: AdapterDict) -> Dict[str, Tuple[Type, bool, Optional[AdapterFunction]]]:
        fields = {name: (typeinfo, flags & protobuf.FLAG_REPEATED)
                  for name, typeinfo, flags
                  in self.message_type.FIELDS.values()}
        processors = {}

        for name, (typeinfo, repeated) in fields.items():
            if name in hints:
                hint = hints[name]
            elif repeated:
                # can't auto-copy repeating fields
                hint = None
            elif isinstance(typeinfo, protobuf.MessageType):
                # missing hint for MessageType - auto adapt structure
                hint = (name, {})
            else:
                # missing hint for primitive type - auto adapt field
                hint = name

            if hint is None:
                action = None

            elif isinstance(hint, str):
                if repeated:
                    raise ValueError('Cannot adapt repeating field {} with str.'.format(name))
                action = self.read_field(hint)

            elif callable(hint):
                action = hint

            else:
                if isinstance(hint, tuple):
                    rename, hint = hint
                else:
                    rename = name

                if isinstance(hint, dict):
                    if repeated:
                        raise ValueError('Cannot adapt repeating field {} with dict.'.format(name))
                    if not issubclass(typeinfo, protobuf.MessageType):
                        raise ValueError('Cannot adapt non-protobuf field {} with dict.'.format(name))
                    action = self.dict_to_adapter(rename, typeinfo, hint)

                elif isinstance(hint, list):
                    if not repeated:
                        raise ValueError('Cannot adapt non-repeating field {} with list.'.format(name))
                    if len(hint) != 1:
                        raise ValueError('Adapter for repeating field {} must be a one-item list.'.format(name))

                    hint = hint[0]
                    if isinstance(hint, dict):
                        if not issubclass(typeinfo, protobuf.MessageType):
                            raise ValueError('Cannot adapt non-protobuf field {} with dict.'.format(name))
                        adapter = self.dict_to_adapter(None, typeinfo, hint)
                    elif callable(hint):
                        adapter = hint
                    else:
                        raise ValueError('Unexpected list item for field {}. Expected dict or callable'.format(name))

                    action = self.map_action(rename, adapter)

                else:
                    raise ValueError('Unexpected second item for field {}. Expected dict or list.'.format(name))

            processors[name] = (typeinfo, repeated, action)

        return processors

    def _coerce_type(self, name: str, value: Any, typeinfo: Type, repeated: bool) -> Any:
        if repeated:
            if not isinstance(value, list):
                raise AdapterError('Expected list for "{}", got {}'.format(name, type(value)))
            return list(self._coerce_type(name, x, typeinfo, False) for x in value)

        if value is None:
            return None

        if issubclass(typeinfo, protobuf.MessageType):
            if not isinstance(value, typeinfo):
                raise AdapterError('Invalid subclass on "{}": expected {}, found {}'.format(name, typeinfo, type(value)))
            return value

        elif typeinfo is protobuf.UVarintType:
            value = int(value)
            if value < 0:
                raise AdapterError('Negative number in unsigned field "{}": {}'.format(name, value))
            return value

        elif typeinfo is protobuf.Sint32Type:
            return int(value)

        elif typeinfo is protobuf.BoolType:
            return bool(value)

        elif typeinfo is protobuf.BytesType:
            if not isinstance(value, (str, bytes)):
                raise AdapterError('Expected str or bytes as a value for "{}", got {}'.format(name, type(value)))
            # todo conversion options
            return binascii.unhexlify(value)

        elif typeinfo is protobuf.UnicodeType:
            # TODO really?
            return str(value)

        else:
            raise RuntimeError('Unknown type encountered on {}: {}'.format(name, typeinfo))

    def __call__(self, data: Dict) -> AdapterType:
        if self.select_field:
            try:
                data = data[self.select_field]
            except KeyError:
                return None

        obj = self.message_type()
        empty_obj = True

        for name, (typeinfo, repeated, action) in self.processors.items():
            if action is None:
                value = [] if repeated else None
            else:
                value = self._coerce_type(name, action(data), typeinfo, repeated)
            if value is not None and value != []:
                empty_obj = False
            setattr(obj, name, value)

        # did we create empty object?
        if empty_obj:
            return None
        else:
            return obj


build_map()
