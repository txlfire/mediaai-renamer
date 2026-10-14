"""受控测试服务：DAV:getetag与HTTP ETag采用相同的带引号序列化。"""

from wsgidav.fs_dav_provider import FileResource, FilesystemProvider


class HttpEtagFileResource(FileResource):
    def get_property_value(self, name):
        value = super().get_property_value(name)
        # WsgiDAV4.3.3内部标识无引号；仅修复XML输出，不改变If-Match的内部判断。
        if name == "{DAV:}getetag" and value and not value.startswith('"'):
            return f'"{value}"'
        return value


class StrictEtagProvider(FilesystemProvider):
    def get_resource_inst(self, path, environ):
        resource = super().get_resource_inst(path, environ)
        if isinstance(resource, FileResource):
            return HttpEtagFileResource(path, environ, resource._file_path)
        return resource
