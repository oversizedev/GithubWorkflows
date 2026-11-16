import base64
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.appstoreconnect.apple.com/v1"
PLATFORMS = {
    "IOS_APP_STORE": "IOS",
    "TVOS_APP_STORE": "IOS",
    "MAC_APP_STORE": "MAC_OS",
    "IOS_APP_ADHOC": "IOS",
    "MAC_APP_DIRECT": "MAC_OS",
}
CERTIFICATE_TYPES = {
    "IOS_APP_STORE": ["DISTRIBUTION", "IOS_DISTRIBUTION"],
    "TVOS_APP_STORE": ["DISTRIBUTION", "IOS_DISTRIBUTION"],
    "IOS_APP_ADHOC": ["DISTRIBUTION", "IOS_DISTRIBUTION"],
    "MAC_APP_STORE": ["DISTRIBUTION", "MAC_APP_DISTRIBUTION"],
    "MAC_APP_DIRECT": ["DEVELOPER_ID_APPLICATION", "DEVELOPER_ID_APPLICATION_G2"],
}
MINIMUM_VALIDITY = datetime.timedelta(days=7)


def fail(message):
    print(f"::error::{message}")
    sys.exit(1)


def base64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def der_integer(data, offset):
    if data[offset] != 0x02:
        fail("Unexpected ECDSA signature encoding")
    length = data[offset + 1]
    value = data[offset + 2 : offset + 2 + length]
    return value.lstrip(b"\x00").rjust(32, b"\x00"), offset + 2 + length


def token(key_path, key_id, issuer_id):
    header = base64url(json.dumps({"alg": "ES256", "kid": key_id, "typ": "JWT"}).encode())
    now = int(time.time())
    claims = {"iss": issuer_id, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"}
    payload = base64url(json.dumps(claims).encode())
    message = f"{header}.{payload}".encode()
    signature = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", key_path],
        input=message,
        capture_output=True,
        check=True,
    ).stdout
    if signature[0] != 0x30 or signature[1] & 0x80:
        fail("Unexpected ECDSA signature encoding")
    r_value, offset = der_integer(signature, 2)
    s_value, _ = der_integer(signature, offset)
    return f"{header}.{payload}.{base64url(r_value + s_value)}"


class Client:
    def __init__(self, key_path, key_id, issuer_id):
        self.jwt = token(key_path, key_id, issuer_id)

    def request(self, method, path, query=None, body=None):
        url = path if path.startswith("https://") else f"{API}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.jwt}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read()
        except urllib.error.HTTPError as error:
            fail(f"{method} {path} failed with HTTP {error.code}: {error.read().decode(errors='replace')}")
        return json.loads(content) if content else {}

    def all(self, path, query):
        items = []
        included = []
        page = self.request("GET", path, query)
        while True:
            items += page.get("data", [])
            included += page.get("included", [])
            next_url = page.get("links", {}).get("next")
            if not next_url:
                return items, included
            page = self.request("GET", next_url)


def normalized_serial(value):
    return value.upper().lstrip("0")


def expiration(profile):
    value = profile["attributes"]["expirationDate"]
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    key_path = os.environ["KEY_PATH"]
    key_id = os.environ["KEY_ID"]
    issuer_id = os.environ["ISSUER_ID"]
    bundle_id = os.environ["BUNDLE_ID"]
    profile_name = os.environ["PROFILE_NAME"]
    profile_type = os.environ["PROFILE_TYPE"]
    serial = normalized_serial(os.environ["CERTIFICATE_SERIAL"])
    if profile_type not in PLATFORMS:
        fail(f"Unsupported profile-type {profile_type}")

    client = Client(key_path, key_id, issuer_id)

    bundles, _ = client.all("/bundleIds", {"filter[identifier]": bundle_id, "limit": 200})
    bundle = next((item for item in bundles if item["attributes"]["identifier"] == bundle_id), None)
    if bundle is None:
        fail(f"Bundle id {bundle_id} is not registered in the developer account")

    certificates, _ = client.all(
        "/certificates",
        {"filter[certificateType]": ",".join(CERTIFICATE_TYPES[profile_type]), "limit": 200},
    )
    certificate = next(
        (item for item in certificates if normalized_serial(item["attributes"]["serialNumber"]) == serial),
        None,
    )
    if certificate is None:
        fail(f"Certificate with serial {serial} from the .p12 is not in the developer account or was revoked")

    profiles, _ = client.all(
        "/profiles",
        {"filter[name]": profile_name, "include": "bundleId,certificates", "limit": 200},
    )
    deadline = datetime.datetime.now(datetime.timezone.utc) + MINIMUM_VALIDITY
    profile = None
    for candidate in profiles:
        attributes = candidate["attributes"]
        if attributes["name"] != profile_name:
            continue
        relationships = candidate.get("relationships", {})
        candidate_bundle = relationships.get("bundleId", {}).get("data") or {}
        candidate_certificates = relationships.get("certificates", {}).get("data") or []
        problems = []
        if attributes["profileState"] != "ACTIVE":
            problems.append(f"state {attributes['profileState']}")
        if attributes["profileType"] != profile_type:
            problems.append(f"type {attributes['profileType']}")
        if candidate_bundle.get("id") != bundle["id"]:
            problems.append("another bundle id")
        if certificate["id"] not in {item["id"] for item in candidate_certificates}:
            problems.append("no current certificate")
        if expiration(candidate) < deadline:
            problems.append(f"expires {attributes['expirationDate']}")
        if not problems and profile is None:
            print(f"Using profile {profile_name} ({candidate['id']}), expires {attributes['expirationDate']}")
            profile = candidate
            continue
        reason = ", ".join(problems) or "duplicate"
        print(f"Deleting profile {profile_name} ({candidate['id']}): {reason}")
        client.request("DELETE", f"/profiles/{candidate['id']}")

    if profile is None:
        print(f"Creating {profile_type} profile {profile_name} for {bundle_id}")
        profile = client.request(
            "POST",
            "/profiles",
            body={
                "data": {
                    "type": "profiles",
                    "attributes": {"name": profile_name, "profileType": profile_type},
                    "relationships": {
                        "bundleId": {"data": {"type": "bundleIds", "id": bundle["id"]}},
                        "certificates": {"data": [{"type": "certificates", "id": certificate["id"]}]},
                    },
                }
            },
        )["data"]
        if "profileContent" not in profile["attributes"]:
            profile = client.request("GET", f"/profiles/{profile['id']}")["data"]

    attributes = profile["attributes"]
    content = base64.b64decode(attributes["profileContent"])
    uuid = attributes["uuid"]
    home = pathlib.Path.home()
    installed = []
    for folder in (
        home / "Library/MobileDevice/Provisioning Profiles",
        home / "Library/Developer/Xcode/UserData/Provisioning Profiles",
    ):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{uuid}.mobileprovision"
        path.write_bytes(content)
        installed.append(str(path))
    print(f"Installed profile {profile_name} ({uuid}), expires {attributes['expirationDate']}")

    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"uuid={uuid}\n")
        output.write(f"name={profile_name}\n")
    with open(os.environ["GITHUB_ENV"], "a") as environment:
        environment.write("PROVISIONING_PROFILE_PATHS<<EOF\n" + "\n".join(installed) + "\nEOF\n")


main()
