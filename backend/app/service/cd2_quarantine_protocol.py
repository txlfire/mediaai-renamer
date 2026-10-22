"""CD2隔离最小线协议投影；仅身份查询和单文件MoveFile，不含正文或删除。"""

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from app.service import cd2_directory_protocol as directory

descriptor = descriptor_pb2.FileDescriptorProto()
descriptor.CopyFrom(directory._file)
descriptor.name = "mediaai_cd2_quarantine.proto"
for message in list(descriptor.message_type):
    if message.name == "RenameFileRequest":
        descriptor.message_type.remove(message)
    elif message.name == "CloudDriveFile":
        message.field.add(name="size", number=4, type=descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
            label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL)

move = descriptor.message_type.add(name="MoveFileRequest")
_field = descriptor_pb2.FieldDescriptorProto
for name, number, kind, repeated, optional in (
    ("theFilePaths", 1, _field.TYPE_STRING, True, False), ("destPath", 2, _field.TYPE_STRING, False, False),
    ("conflictPolicy", 3, _field.TYPE_INT32, False, True), ("moveAcrossClouds", 4, _field.TYPE_BOOL, False, True),
    ("handleConflictRecursively", 5, _field.TYPE_BOOL, False, True)):
    field = move.field.add(name=name, number=number, type=kind, label=_field.LABEL_REPEATED if repeated else _field.LABEL_OPTIONAL)
    if optional:
        move.oneof_decl.add(name="_" + name)
        field.oneof_index = len(move.oneof_decl)-1
        field.proto3_optional = True
pool = descriptor_pool.DescriptorPool()
pool.Add(descriptor)
CloudDriveFile = message_factory.GetMessageClass(pool.FindMessageTypeByName("clouddrive.CloudDriveFile"))
MoveFileRequest = message_factory.GetMessageClass(pool.FindMessageTypeByName("clouddrive.MoveFileRequest"))
FileOperationResult = message_factory.GetMessageClass(pool.FindMessageTypeByName("clouddrive.FileOperationResult"))
FindFileByPathRequest = message_factory.GetMessageClass(pool.FindMessageTypeByName("clouddrive.FindFileByPathRequest"))
