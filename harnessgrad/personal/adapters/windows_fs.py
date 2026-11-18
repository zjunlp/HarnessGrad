"""Read-only directory-relative handles for local Windows project surfaces."""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
import msvcrt
import os
from pathlib import PureWindowsPath
import re
import stat
import struct

_kernel = c.WinDLL('kernel32', use_last_error=True)
_nt = c.WinDLL('ntdll')

FILE_READ_DATA = 0x0001  # FILE_LIST_DIRECTORY for a directory.
FILE_READ_ATTRIBUTES = 0x0080
SYNCHRONIZE = 0x00100000
FILE_SHARE_READ = 0x0001
FILE_SHARE_WRITE = 0x0002
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_OPEN = 1
FILE_DIRECTORY_FILE = 0x0001
FILE_NON_DIRECTORY_FILE = 0x0040
FILE_SYNCHRONOUS_IO_NONALERT = 0x0020
FILE_OPEN_REPARSE_POINT = 0x00200000
OBJ_CASE_INSENSITIVE = 0x0040
OBJ_DONT_REPARSE = 0x1000
FILE_ATTRIBUTE_DIRECTORY = 0x0010
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
FILE_ATTRIBUTE_READONLY = 0x0001
FILE_TYPE_DISK = 1
DRIVE_FIXED = 3
ERROR_NO_MORE_FILES = 18
FILE_BASIC_INFO = 0
FILE_STANDARD_INFO = 1
FILE_FULL_DIRECTORY_INFO = 14
FILE_FULL_DIRECTORY_RESTART_INFO = 15
FILE_ID_INFO = 18


class _UnicodeString(c.Structure):
    _fields_ = [('length', w.USHORT), ('capacity', w.USHORT), ('buffer', c.c_void_p)]


class _ObjectAttributes(c.Structure):
    _fields_ = [('length', w.ULONG), ('root', w.HANDLE), ('name', c.POINTER(_UnicodeString)),
                ('attributes', w.ULONG), ('security', c.c_void_p), ('quality', c.c_void_p)]


class _IoStatusBlock(c.Structure):
    _fields_ = [('status_or_pointer', c.c_void_p), ('information', c.c_size_t)]


class _BasicInfo(c.Structure):
    _fields_ = [('created', c.c_int64), ('accessed', c.c_int64), ('written', c.c_int64),
                ('changed', c.c_int64), ('attributes', w.DWORD)]


class _StandardInfo(c.Structure):
    _fields_ = [('allocation', c.c_int64), ('eof', c.c_int64), ('links', w.DWORD),
                ('delete_pending', c.c_ubyte), ('directory', c.c_ubyte)]


class _IdInfo(c.Structure):
    _fields_ = [('volume', c.c_uint64), ('file_id', c.c_ubyte * 16)]


@dataclass(frozen=True)
class _FileStat:
    st_dev: int
    st_ino: int
    st_mode: int
    st_nlink: int
    st_size: int
    st_mtime_ns: int
    st_ctime_ns: int


_create = _nt.NtCreateFile
_create.argtypes = [c.POINTER(w.HANDLE), w.ULONG, c.POINTER(_ObjectAttributes),
                    c.POINTER(_IoStatusBlock), c.c_void_p, w.ULONG, w.ULONG,
                    w.ULONG, w.ULONG, c.c_void_p, w.ULONG]
_create.restype = w.LONG
_dos_error = _nt.RtlNtStatusToDosError
_dos_error.argtypes = [w.LONG]
_dos_error.restype = w.ULONG
_info = _kernel.GetFileInformationByHandleEx
_info.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
_info.restype = w.BOOL
_close = _kernel.CloseHandle
_close.argtypes = [w.HANDLE]
_close.restype = w.BOOL
_drive_type = _kernel.GetDriveTypeW
_drive_type.argtypes = [w.LPCWSTR]
_drive_type.restype = w.UINT
_file_type = _kernel.GetFileType
_file_type.argtypes = [w.HANDLE]
_file_type.restype = w.DWORD
_create_root = _kernel.CreateFileW
_create_root.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
_create_root.restype = w.HANDLE
_drive_device = _kernel.QueryDosDeviceW
_drive_device.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
_drive_device.restype = w.DWORD


def _query(handle: int, kind: int, value):
    if not _info(handle, kind, c.byref(value), c.sizeof(value)):
        raise c.WinError(c.get_last_error())
    return value


def _component(name: str) -> str:
    if (not name or name.endswith(('.', ' ')) or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or PureWindowsPath(name).is_reserved()):
        raise OSError('unsafe Windows path component')
    return name


def _open(name: str, parent: int | None, *, directory: bool) -> int:
    encoded = name.encode('utf-16-le')
    # ObjectAttributes names are counted UTF-16 strings. A rooted call gets only one component.
    buffer = c.create_string_buffer(encoded + b'\0\0')
    filename = _UnicodeString(len(encoded), len(encoded) + 2, c.addressof(buffer))
    attributes = _ObjectAttributes(c.sizeof(_ObjectAttributes), parent, c.pointer(filename),
                                  OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE, None, None)
    handle = w.HANDLE()
    options = FILE_DIRECTORY_FILE if directory else FILE_NON_DIRECTORY_FILE
    share = FILE_SHARE_READ | FILE_SHARE_WRITE if directory else FILE_SHARE_READ
    status = _create(c.byref(handle), FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
                     c.byref(attributes), c.byref(_IoStatusBlock()), None, 0, share, FILE_OPEN,
                     options | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT, None, 0)
    if status < 0:
        raise c.WinError(_dos_error(status))
    return _checked_handle(handle.value, directory=directory)


def _checked_handle(handle: int, *, directory: bool) -> int:
    try:
        kind = _query(handle, FILE_BASIC_INFO, _BasicInfo()).attributes
        if (_file_type(handle) != FILE_TYPE_DISK or kind & FILE_ATTRIBUTE_REPARSE_POINT
                or bool(kind & FILE_ATTRIBUTE_DIRECTORY) != directory):
            raise OSError('selected Windows object is linked or has the wrong type')
    except BaseException:
        close_directory(handle)
        raise
    return handle


def open_directory(name: str, parent: int | None = None) -> int:
    if parent is not None:
        return _open(_component(name), parent, directory=True)
    if not re.fullmatch(r'[A-Za-z]:\\', name) or _drive_type(name) != DRIVE_FIXED:
        raise OSError('selected Windows project must use a local fixed drive')
    target = c.create_unicode_buffer(2048)
    if not _drive_device(name[:2], target, len(target)):
        raise c.WinError(c.get_last_error())
    if not re.fullmatch(r'\\Device\\HarddiskVolume\d+', target.value, re.IGNORECASE):
        raise OSError('selected Windows drive must resolve directly to a local volume')
    # Open the validated volume, not a DOS alias that can be rebound between calls.
    volume_path = '\\\\?\\GLOBALROOT' + target.value + '\\'
    handle = _create_root(volume_path, FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
                          FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING,
                          FILE_FLAG_BACKUP_SEMANTICS | FILE_OPEN_REPARSE_POINT, None)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    return _checked_handle(handle, directory=True)


def close_directory(handle: int) -> None:
    _close(handle)


def open_file(name: str, parent: int) -> int:
    handle = _open(_component(name), parent, directory=False)
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close_directory(handle)
        raise


def stat_directory(handle: int) -> _FileStat:
    info = _query(handle, FILE_ID_INFO, _IdInfo())
    return _FileStat(info.volume, int.from_bytes(info.file_id, 'little'), stat.S_IFDIR, 1, 0, 0, 0)


def stat_file(fd: int) -> _FileStat:
    handle = msvcrt.get_osfhandle(fd)
    info = _query(handle, FILE_ID_INFO, _IdInfo())
    standard = _query(handle, FILE_STANDARD_INFO, _StandardInfo())
    basic = _query(handle, FILE_BASIC_INFO, _BasicInfo())
    if standard.delete_pending or standard.directory or basic.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError('selected Windows file is no longer a regular file')
    permissions = 0o444 if basic.attributes & FILE_ATTRIBUTE_READONLY else 0o666
    return _FileStat(info.volume, int.from_bytes(info.file_id, 'little'), stat.S_IFREG | permissions,
                     standard.links, standard.eof, basic.written * 100, basic.changed * 100)


# FILE_FULL_DIR_INFO's fixed fields are followed by FileNameLength bytes of UTF-16LE.
_DIRECTORY_ENTRY = struct.Struct('<II6qIII')


def directory_names(handle: int):
    buffer = c.create_string_buffer(8192)
    kind = FILE_FULL_DIRECTORY_RESTART_INFO
    while _info(handle, kind, buffer, c.sizeof(buffer)):
        kind = FILE_FULL_DIRECTORY_INFO
        offset = 0
        while True:
            skip, *_, length, _ea = _DIRECTORY_ENTRY.unpack_from(buffer, offset)
            start = offset + _DIRECTORY_ENTRY.size
            if (length % 2 or not length or start + length > len(buffer)
                    or (skip and (skip % 8 or offset + skip < start + length
                                  or offset + skip + _DIRECTORY_ENTRY.size > len(buffer)))):
                raise OSError('malformed Windows directory enumeration')
            name = bytes(buffer[start:start + length]).decode('utf-16-le', errors='surrogatepass')
            if name not in ('.', '..'):
                yield name
            if not skip:
                break
            offset += skip
    error = c.get_last_error()
    if error != ERROR_NO_MORE_FILES:
        raise c.WinError(error)
