"""通过平台原语移动文件，禁止覆盖已有目标。"""

import ctypes
import errno
import os
from pathlib import Path
import sys
import shutil

from app.utils.file_permissions import copyFileAccessControl


def moveFileNoReplace(sourcePath: str | Path, targetPath: str | Path) -> None:
    source = os.fspath(sourcePath)
    target = os.fspath(targetPath)
    if os.path.abspath(source) == os.path.abspath(target):
        return
    if os.name == "nt":
        os.rename(source, target)
        return
    if not sys.platform.startswith("linux"):
        raise OSError(errno.ENOTSUP, "当前平台未提供已验证的禁止覆盖移动能力")
    try:
        renameNoReplace = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, "当前运行库不支持禁止覆盖移动") from exc
    renameNoReplace.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameNoReplace.restype = ctypes.c_int
    # AT_FDCWD 与 RENAME_NOREPLACE：不降级为会覆盖目标的普通 rename。
    if renameNoReplace(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        errorCode = ctypes.get_errno()
        raise OSError(errorCode, os.strerror(errorCode), target)


def copyFileNoReplace(sourcePath: Path, targetPath: Path, expectedIdentity: list[int]) -> list[int]:
    def identity(info):
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]

    with sourcePath.open("rb") as reader:
        if identity(os.fstat(reader.fileno())) != expectedIdentity:
            raise ValueError("复制前源文件已变化")
        descriptor = os.open(targetPath, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as writer:
            # 写入内容前保护 ACL，失败保留源文件，不覆盖或清理目标。
            copyFileAccessControl(sourcePath, targetPath)
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
            if identity(os.fstat(reader.fileno())) != expectedIdentity:
                raise ValueError("复制期间源文件已变化")
            shutil.copystat(sourcePath, targetPath)
            targetIdentity = identity(os.fstat(writer.fileno()))
            if identity(targetPath.lstat()) != targetIdentity:
                raise ValueError("复制目标被替换，停止迁移")
            return targetIdentity
