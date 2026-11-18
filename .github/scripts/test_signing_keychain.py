import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / '.github/actions/signing-keychain'
CERTIFICATE_HASHES = {
    'AppleWWDRCAG3': 'dcf21878c77f4198e4b4614f03d696d89c66c66008d4244e1b99161aac91601f',
    'DeveloperIDG2CA': 'f16cd3c54c7f83cea4bf1a3e6a0819c8aaa8e4a1528fd144715f350643d2df3a',
    'DeveloperIDCA': '7afc9d01a62f03a2de9637936d4afe68090d2de18d03f29c88cfb0b1ba63587f',
}
IDENTITY = 'F' * 40


STUB = '''import json
import os
from pathlib import Path
import sys

command = Path(sys.argv[0]).name
arguments = sys.argv[1:]
with open(os.environ['COMMAND_LOG'], 'a') as stream:
    stream.write(json.dumps([command, *arguments]) + '\\n')
if command == 'security':
    if arguments[0] == 'list-keychains' and '-s' not in arguments:
        print('"/tmp/test-login.keychain-db"')
    elif arguments[0] == 'import':
        if not Path(arguments[1]).is_file():
            raise SystemExit('Missing import file: ' + arguments[1])
    elif arguments[0] == 'find-identity':
        if os.environ['VALID_IDENTITY'] == '1':
            print('  1) ' + 'F' * 40 + ' "Apple Distribution: Example (TESTTEAM)"')
        else:
            print('  0 valid identities found')
    elif arguments[0] == 'find-certificate':
        print('SHA-1 hash: ' + 'F' * 40)
        print('Test certificate')
elif command == 'openssl':
    if arguments[0] == 'rand':
        print('test-keychain-password')
    elif arguments[0] == 'x509':
        sys.stdin.read()
        print('serial=00AABB')
    else:
        raise SystemExit('Unexpected openssl arguments: ' + repr(arguments))
elif command == 'curl':
    if os.environ['ALLOW_DOWNLOAD'] != '1':
        print('curl: (6) Could not resolve host: www.apple.com', file=sys.stderr)
        raise SystemExit(6)
    Path(arguments[arguments.index('-o') + 1]).write_text('Test downloaded intermediate')
else:
    raise SystemExit('Unexpected command: ' + command)
'''


class SigningKeychainTests(unittest.TestCase):
    def run_action(self, intermediates, allow_download=False, valid_identity=True):
        with tempfile.TemporaryDirectory(prefix='signing-keychain-test-') as directory:
            temporary = Path(directory)
            binary = temporary / 'bin'
            binary.mkdir()
            for command in ('security', 'openssl', 'curl'):
                executable = binary / command
                executable.write_text('#!' + sys.executable + '\n' + STUB)
                executable.chmod(0o755)
            script = textwrap.dedent((ACTION / 'action.yml').read_text().split('      run: |\n', 1)[1])
            environment = dict(
                os.environ,
                PATH=str(binary) + os.pathsep + os.environ['PATH'],
                RUNNER_TEMP=str(temporary),
                GITHUB_ENV=str(temporary / 'environment'),
                GITHUB_OUTPUT=str(temporary / 'output'),
                COMMAND_LOG=str(temporary / 'commands'),
                P12_BASE64=base64.b64encode(b'test identity').decode(),
                P12_PASSWORD='test-p12-password',
                CERTIFICATE='Apple Distribution',
                TEAM_ID='TESTTEAM',
                INTERMEDIATES=intermediates,
                INTERMEDIATE_CERTIFICATES_PATH=str(ACTION / 'certificates'),
                ALLOW_DOWNLOAD=str(int(allow_download)),
                VALID_IDENTITY=str(int(valid_identity)),
            )
            result = subprocess.run(['bash', '-c', script], env=environment, capture_output=True, text=True)
            calls = [json.loads(line) for line in (temporary / 'commands').read_text().splitlines()]
            output = temporary / 'output'
            return result, calls, output.read_text() if output.exists() else ''

    def test_default_intermediates_import_without_network(self):
        result, calls, output = self.run_action(' '.join(CERTIFICATE_HASHES))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[0] == 'curl' for call in calls))
        imports = [call[2] for call in calls if call[:2] == ['security', 'import']]
        for name in CERTIFICATE_HASHES:
            self.assertIn(str(ACTION / 'certificates' / (name + '.cer')), imports)
        self.assertIn('identity=' + IDENTITY, output)
        self.assertIn('serial=00AABB', output)

    def test_custom_intermediate_downloads_from_apple(self):
        result, calls, output = self.run_action('AppleWWDRCAG3 CustomIntermediate', allow_download=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        downloads = [call for call in calls if call[0] == 'curl']
        self.assertEqual(len(downloads), 1)
        self.assertIn('https://www.apple.com/certificateauthority/CustomIntermediate.cer', downloads[0])
        self.assertIn('identity=' + IDENTITY, output)

    def test_custom_download_failure_stops_signing(self):
        result, calls, output = self.run_action('CustomIntermediate')
        self.assertEqual(result.returncode, 6)
        self.assertNotIn('identity=', output)
        self.assertFalse(any(call[:2] == ['security', 'find-identity'] for call in calls))

    def test_invalid_identity_is_rejected(self):
        result, calls, output = self.run_action(' '.join(CERTIFICATE_HASHES), valid_identity=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('No valid Apple Distribution identity of team TESTTEAM', result.stdout)
        self.assertNotIn('identity=', output)

    def test_bundled_certificates_have_pinned_hashes_and_are_not_expired(self):
        for name, expected_hash in CERTIFICATE_HASHES.items():
            with self.subTest(certificate=name):
                certificate = ACTION / 'certificates' / (name + '.cer')
                self.assertEqual(hashlib.sha256(certificate.read_bytes()).hexdigest(), expected_hash)
                result = subprocess.run(
                    ['openssl', 'x509', '-inform', 'DER', '-in', str(certificate), '-noout', '-checkend', '0'],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
