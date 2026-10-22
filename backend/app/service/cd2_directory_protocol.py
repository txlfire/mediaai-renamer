"""CD2 1.1.2目录动作最小线协议投影；不包含移动、删除或文件正文接口。"""

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

_file = descriptor_pb2.FileDescriptorProto(name="mediaai_cd2_directory.proto", package="clouddrive", syntax="proto3")
_field = descriptor_pb2.FieldDescriptorProto


def _message(name, fields):
    message = _file.message_type.add(name=name)
    for fieldName, number, fieldType, typeName, repeated in fields:
        field = message.field.add(name=fieldName, number=number, type=fieldType,
            label=_field.LABEL_REPEATED if repeated else _field.LABEL_OPTIONAL)
        if typeName:
            field.type_name = ".clouddrive." + typeName


_message("CloudAPI", [("isLocked", 4, _field.TYPE_BOOL, None, False), ("readOnly", 12, _field.TYPE_BOOL, None, False)])
_message("CloudDriveFile", [
    ("id", 1, _field.TYPE_STRING, None, False), ("name", 2, _field.TYPE_STRING, None, False),
    ("fullPathName", 3, _field.TYPE_STRING, None, False), ("fileType", 5, _field.TYPE_INT32, None, False),
    ("CloudAPI", 9, _field.TYPE_MESSAGE, "CloudAPI", False),
    *[(name, number, _field.TYPE_BOOL, None, False) for name, number in (
        ("isDirectory", 30), ("isRoot", 31), ("isCloudRoot", 32), ("isSearchResult", 35),
        ("isForbidden", 36), ("isLocal", 37), ("readOnly", 80), ("isArchiveFolder", 81))],
    ("fileEncryptionType", 71, _field.TYPE_INT32, None, False),
])
_message("FindFileByPathRequest", [("parentPath", 1, _field.TYPE_STRING, None, False), ("path", 2, _field.TYPE_STRING, None, False)])
_message("RenameFileRequest", [("theFilePath", 1, _field.TYPE_STRING, None, False), ("newName", 2, _field.TYPE_STRING, None, False)])
_message("FileOperationResult", [("success", 1, _field.TYPE_BOOL, None, False),
    ("errorMessage", 2, _field.TYPE_STRING, None, False), ("resultFilePaths", 3, _field.TYPE_STRING, None, True)])

_pool = descriptor_pool.DescriptorPool()
_pool.Add(_file)


def _type(name):
    return message_factory.GetMessageClass(_pool.FindMessageTypeByName("clouddrive." + name))


CloudAPI = _type("CloudAPI")
CloudDriveFile = _type("CloudDriveFile")
FindFileByPathRequest = _type("FindFileByPathRequest")
RenameFileRequest = _type("RenameFileRequest")
FileOperationResult = _type("FileOperationResult")
