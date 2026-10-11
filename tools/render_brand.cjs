// SPDX-License-Identifier: Apache-2.0
// Optional export helper. Install sharp, or pass its installed module path.
const sharp = require(process.argv[2] || 'sharp');
const path = require('node:path');
const assets = path.join(__dirname, '..', 'website', 'assets');
(async () => {
  await sharp(path.join(assets, 'meshticulum-logo.svg')).resize(1024, 1024).png()
    .toFile(path.join(assets, 'meshticulum-logo.png'));
  await sharp(path.join(assets, 'social-card.svg')).png()
    .toFile(path.join(assets, 'social-card.png'));
  console.log('Rendered logo (1024×1024) and social card (1200×630).');
})().catch(error => { console.error(error.message); process.exitCode = 1; });
