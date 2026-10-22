"""Bounded collection of actor outputs. These bytes are submissions, never gold."""
import base64
import hashlib
import os
import stat
from pathlib import Path


def collect(root, paths, *, max_bytes=16*1024*1024):
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
        raise ValueError('artifact max_bytes must be positive or null')
    result={}
    for name in paths:
        rel=Path(name)
        if not isinstance(name,str) or not rel.parts or rel.is_absolute() or '..' in rel.parts:
            raise ValueError('artifact paths must be relative and cannot escape')
        path=Path(root)/rel
        if any(p.is_symlink() for p in (path,*path.parents)):
            raise ValueError('artifact paths cannot use symlinks')
        try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except FileNotFoundError:
            result[name]={'status':'missing'};continue
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                raise ValueError('artifact must be a regular file without hardlink aliases')
            data=stream.read() if max_bytes is None else stream.read(max_bytes+1)
        if max_bytes is not None:
            if len(data)>max_bytes:raise ValueError('artifact exceeds byte limit')
            max_bytes-=len(data)
        result[name]={'status':'submitted','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                      'base64':base64.b64encode(data).decode()}
    return result
