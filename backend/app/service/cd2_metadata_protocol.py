"""官方 clouddrive.proto 的只读线协议投影，使用标准 protobuf 编解码。

核对来源及协议摘要见 cloud-drive/c0-implementation.md；不定义下载或写入 RPC。
投影不是完整服务 SDK，新增权限字段必须拒绝而不是丢弃后放行。
"""

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

PERMISSION_NAMES = (
    "allow_list", "allow_search", "allow_list_local", "allow_create_folder", "allow_create_file",
    "allow_write", "allow_read", "allow_rename", "allow_move", "allow_copy", "allow_delete",
    "allow_delete_permanently", "allow_create_encrypt", "allow_unlock_encrypted", "allow_lock_encrypted",
    "allow_add_offline_download", "allow_list_offline_downloads", "allow_modify_offline_downloads",
    "allow_shared_links", "allow_view_properties", "allow_get_space_info", "allow_view_runtime_info",
    "allow_get_memberships", "allow_modify_memberships", "allow_get_mounts", "allow_modify_mounts",
    "allow_get_transfer_tasks", "allow_modify_transfer_tasks", "allow_get_cloud_apis", "allow_modify_cloud_apis",
    "allow_get_system_settings", "allow_modify_system_settings", "allow_get_backups", "allow_modify_backups",
    "allow_get_dav_config", "allow_modify_dav_config", "allow_token_management", "allow_get_account_info",
    "allow_modify_account", "allow_service_control", "allow_push_message", "allow_get_archive_folders",
    "allow_modify_archive_folders",
)

_file = descriptor_pb2.FileDescriptorProto(name="mediaai_cd2_metadata.proto", package="clouddrive", syntax="proto3")
_field = descriptor_pb2.FieldDescriptorProto


def _message(name, fields):
    message = _file.message_type.add(name=name)
    for fieldName, number, fieldType, repeated, typeName in fields:
        entry = message.field.add(name=fieldName, number=number, type=fieldType,
                                  label=_field.LABEL_REPEATED if repeated else _field.LABEL_OPTIONAL)
        if typeName:
            entry.type_name = ".clouddrive." + typeName


_message("Empty", [])
_message("StringValue", [("value", 1, _field.TYPE_STRING, False, None)])
_message("TokenPermissions", [(name, number, _field.TYPE_BOOL, False, None)
                              for number, name in enumerate(PERMISSION_NAMES, start=1)])
_message("TokenInfo", [
    ("token", 1, _field.TYPE_STRING, False, None),
    ("rootDir", 2, _field.TYPE_STRING, False, None),
    ("permissions", 3, _field.TYPE_MESSAGE, False, "TokenPermissions"),
    ("expires_in", 4, _field.TYPE_UINT64, False, None),
])
_message("RuntimeInfo", [(name, number, _field.TYPE_STRING, False, None)
                         for number, name in enumerate(("productName", "productVersion", "CloudAPIVersion", "osInfo"), 1)])
_message("ListSubFileRequest", [
    ("path", 1, _field.TYPE_STRING, False, None),
    ("forceRefresh", 2, _field.TYPE_BOOL, False, None),
    ("checkExpires", 3, _field.TYPE_BOOL, False, None),
])
# 官方此字段是optional；显式false必须保留presence，不能退化成服务端默认策略。
_listRequest = _file.message_type[-1]
_listRequest.oneof_decl.add(name="_checkExpires")
_listRequest.field[-1].oneof_index = 0
_listRequest.field[-1].proto3_optional = True
_message("CloudDriveFile", [
    ("id", 1, _field.TYPE_STRING, False, None),
    ("name", 2, _field.TYPE_STRING, False, None),
    ("fullPathName", 3, _field.TYPE_STRING, False, None),
    ("size", 4, _field.TYPE_INT64, False, None),
    # 枚举在线上为 varint；保留整数值并由连接器拒绝未知类型。
    ("fileType", 5, _field.TYPE_INT32, False, None),
    *[(name, number, _field.TYPE_BOOL, False, None) for name, number in (
        ("isDirectory", 30), ("isRoot", 31), ("isCloudRoot", 32),
        ("isSearchResult", 35), ("isForbidden", 36), ("isLocal", 37), ("isArchiveFolder", 81))],
])
_message("SubFilesReply", [("subFiles", 1, _field.TYPE_MESSAGE, True, "CloudDriveFile")])

_pool = descriptor_pool.DescriptorPool()
_pool.Add(_file)


def _type(name):
    return message_factory.GetMessageClass(_pool.FindMessageTypeByName("clouddrive." + name))


Empty = _type("Empty")
StringValue = _type("StringValue")
TokenPermissions = _type("TokenPermissions")
TokenInfo = _type("TokenInfo")
RuntimeInfo = _type("RuntimeInfo")
ListSubFileRequest = _type("ListSubFileRequest")
CloudDriveFile = _type("CloudDriveFile")
SubFilesReply = _type("SubFilesReply")
