"""Personnalise le projet Android généré par Capacitor (exécuté par GitHub Actions).

- icônes AMUNTCHI (à partir du logo)
- écran de démarrage : logo centré sur fond blanc, aux dimensions de chaque image
- permission appareil photo (photos des produits)
- numéro de version
- clé de signature fixe : chaque nouvelle version s'installe par-dessus l'ancienne
  sans perdre les données du téléphone
"""
import re
import shutil
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / 'android' / 'app' / 'src' / 'main' / 'res'
MANIFEST = ROOT / 'android' / 'app' / 'src' / 'main' / 'AndroidManifest.xml'
GRADLE = ROOT / 'android' / 'app' / 'build.gradle'
VERSION_NAME = '5.0'
VERSION_CODE = 50

# 1. Icônes
for src in (ROOT / 'build' / 'res').rglob('*.png'):
    dest = RES / src.relative_to(ROOT / 'build' / 'res')
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
print('Icônes copiées')

# 2. Écrans de démarrage
logo = Image.open(ROOT / 'build' / 'logo_square.png').convert('RGB')
for splash in RES.rglob('splash.png'):
    w, h = Image.open(splash).size
    side = int(min(w, h) * 0.45)
    img = Image.new('RGB', (w, h), 'white')
    img.paste(logo.resize((side, side), Image.LANCZOS), ((w - side) // 2, (h - side) // 2))
    img.save(splash)
print('Écrans de démarrage remplacés')

# 3. Permission appareil photo
m = MANIFEST.read_text(encoding='utf-8')
if 'android.permission.CAMERA' not in m:
    m = m.replace('</manifest>', '    <uses-permission android:name="android.permission.CAMERA" />\n</manifest>')
    MANIFEST.write_text(m, encoding='utf-8')
print('Permission appareil photo ajoutée')

# 4. Version + signature fixe
g = GRADLE.read_text(encoding='utf-8')
g = re.sub(r'versionCode\s+\d+', f'versionCode {VERSION_CODE}', g)
g = re.sub(r'versionName\s+"[^"]*"', f'versionName "{VERSION_NAME}"', g)
if 'amuntchiKey' not in g:
    g = g.replace('android {', '''android {
    signingConfigs {
        amuntchiKey {
            storeFile file("../../build/amuntchi.keystore")
            storePassword "android"
            keyAlias "androiddebugkey"
            keyPassword "android"
        }
    }''', 1)
    g = re.sub(r'(buildTypes\s*\{\s*release\s*\{)', r'\1\n            signingConfig signingConfigs.amuntchiKey', g, count=1)
    g = re.sub(r'(buildTypes\s*\{)', r'\1\n        debug {\n            signingConfig signingConfigs.amuntchiKey\n        }', g, count=1)
GRADLE.write_text(g, encoding='utf-8')
print('Version et signature configurées')
print(g[:1500])
