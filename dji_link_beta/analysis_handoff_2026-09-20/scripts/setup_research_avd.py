from pathlib import Path
import hashlib
import zipfile

root = Path(__file__).resolve().parent
archive = root / 'google-apis-35.zip'
with archive.open('rb') as f:
    digest = hashlib.file_digest(f, 'sha1').hexdigest()
assert digest == '0103e6dab21290c4b9d16550a3ce99476f884eef', digest
dest = root / 'google-apis-35'
with zipfile.ZipFile(archive) as z:
    for name in z.namelist():
        assert (dest / name).resolve().is_relative_to(dest.resolve())
    z.extractall(dest)
avdroot = root / 'avd'
avd = avdroot / 'DJI_Research_35.avd'
avd.mkdir(exist_ok=True)
source = Path.home() / '.android/avd/Pixel_9.avd/config.ini'
config = dict(line.split('=', 1) for line in source.read_text().splitlines() if '=' in line)
config.update({
    'AvdId': 'DJI_Research_35', 'avd.ini.displayname': 'DJI Research API 35',
    'image.sysdir.1': str(dest / 'x86_64') + '/', 'target': 'android-35',
    'tag.display': 'Google APIs', 'tag.displaynames': 'Google APIs',
    'tag.id': 'google_apis', 'tag.ids': 'google_apis', 'PlayStore.enabled': 'false',
    'hw.ramSize': '4096', 'hw.cpu.ncore': '4', 'hw.gpu.mode': 'swiftshader',
    'fastboot.forceColdBoot': 'yes', 'fastboot.forceFastBoot': 'no',
    'disk.dataPartition.size': '8G',
})
(avd / 'config.ini').write_text('\n'.join(f'{k}={v}' for k,v in config.items())+'\n')
(avdroot / 'DJI_Research_35.ini').write_text('avd.ini.encoding=UTF-8\npath='+str(avd)+'\ntarget=android-35\n')
print(avd)
