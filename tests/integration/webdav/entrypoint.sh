#!/bin/sh
set -eu

certDir="/certs"
dataDir="/data"
configPath="/tmp/wsgidav.yaml"
extensionPath="/tmp/server-ext.cnf"

mkdir -p "${certDir}" "${dataDir}"
rm -f "${certDir}/ca.key" "${certDir}/ca.crt" "${certDir}/server.key" \
  "${certDir}/server.csr" "${certDir}/server.crt" "${certDir}/ca.srl"

openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
  -keyout "${certDir}/ca.key" \
  -out "${certDir}/ca.crt" \
  -days 2 \
  -subj "/CN=MediaAI WebDAV Test CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" >/dev/null 2>&1

cat >"${extensionPath}" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
subjectAltName=DNS:localhost,IP:127.0.0.1,DNS:webdav
extendedKeyUsage=serverAuth
EOF

openssl req -newkey rsa:2048 -sha256 -nodes \
  -keyout "${certDir}/server.key" \
  -out "${certDir}/server.csr" \
  -subj "/CN=localhost" >/dev/null 2>&1

openssl x509 -req \
  -in "${certDir}/server.csr" \
  -CA "${certDir}/ca.crt" \
  -CAkey "${certDir}/ca.key" \
  -CAcreateserial \
  -out "${certDir}/server.crt" \
  -days 2 \
  -sha256 \
  -extfile "${extensionPath}" >/dev/null 2>&1

chmod 600 "${certDir}/ca.key" "${certDir}/server.key"
chmod 644 "${certDir}/ca.crt" "${certDir}/server.crt"

cat >"${configPath}" <<EOF
host: 0.0.0.0
port: 9443
server: cheroot
ssl_certificate: ${certDir}/server.crt
ssl_private_key: ${certDir}/server.key
ssl_certificate_chain: ${certDir}/ca.crt
provider_mapping:
  "/": "${dataDir}"
http_authenticator:
  accept_basic: true
  accept_digest: false
  default_to_digest: false
simple_dc:
  user_mapping:
    "*":
      "${WEBDAV_USERNAME}":
        password: "${WEBDAV_PASSWORD}"
dir_browser:
  enable: false
verbose: 2
EOF

exec wsgidav --config="${configPath}"
