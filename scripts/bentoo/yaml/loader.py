# coding: utf-8

from __future__ import absolute_import


from bentoo.yaml.reader import Reader
from bentoo.yaml.scanner import Scanner, RoundTripScanner
from bentoo.yaml.parser import Parser, RoundTripParser
from bentoo.yaml.composer import Composer
from bentoo.yaml.constructor import BaseConstructor, SafeConstructor, Constructor, \
    RoundTripConstructor
from bentoo.yaml.resolver import VersionedResolver

if False:  # MYPY
    from typing import Any, Dict, List                          # NOQA
    from bentoo.yaml.compat import StreamTextType, VersionType  # NOQA

__all__ = ['BaseLoader', 'SafeLoader', 'Loader', 'RoundTripLoader']


class BaseLoader(Reader, Scanner, Parser, Composer, BaseConstructor, VersionedResolver):
    def __init__(self, stream, version=None, preserve_quotes=None):
        # type: (StreamTextType, VersionType, bool) -> None
        Reader.__init__(self, stream, loader=self)
        Scanner.__init__(self, loader=self)
        Parser.__init__(self, loader=self)
        Composer.__init__(self, loader=self)
        BaseConstructor.__init__(self, loader=self)
        VersionedResolver.__init__(self, version, loader=self)


class SafeLoader(Reader, Scanner, Parser, Composer, SafeConstructor, VersionedResolver):
    def __init__(self, stream, version=None, preserve_quotes=None):
        # type: (StreamTextType, VersionType, bool) -> None
        Reader.__init__(self, stream, loader=self)
        Scanner.__init__(self, loader=self)
        Parser.__init__(self, loader=self)
        Composer.__init__(self, loader=self)
        SafeConstructor.__init__(self, loader=self)
        VersionedResolver.__init__(self, version, loader=self)


class Loader(Reader, Scanner, Parser, Composer, Constructor, VersionedResolver):
    def __init__(self, stream, version=None, preserve_quotes=None):
        # type: (StreamTextType, VersionType, bool) -> None
        Reader.__init__(self, stream, loader=self)
        Scanner.__init__(self, loader=self)
        Parser.__init__(self, loader=self)
        Composer.__init__(self, loader=self)
        Constructor.__init__(self, loader=self)
        VersionedResolver.__init__(self, version, loader=self)


class RoundTripLoader(Reader, RoundTripScanner, RoundTripParser, Composer,
                      RoundTripConstructor, VersionedResolver):
    def __init__(self, stream, version=None, preserve_quotes=None):
        # type: (StreamTextType, VersionType, bool) -> None
        # self.reader = Reader.__init__(self, stream)
        Reader.__init__(self, stream, loader=self)
        RoundTripScanner.__init__(self, loader=self)
        RoundTripParser.__init__(self, loader=self)
        Composer.__init__(self, loader=self)
        RoundTripConstructor.__init__(self, preserve_quotes=preserve_quotes, loader=self)
        VersionedResolver.__init__(self, version, loader=self)
