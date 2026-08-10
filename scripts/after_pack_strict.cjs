const fs = require('node:fs/promises');
const path = require('node:path');

module.exports = async function afterPackStrict(context) {
  if (context.electronPlatformName !== 'darwin') {
    return;
  }

  const resources = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`,
    'Contents',
    'Resources',
  );
  const entries = await fs.readdir(resources);
  for (const entry of entries) {
    if (
      entry === 'default_app.asar'
      || entry === 'app-update.yml'
      || entry.endsWith('.lproj')
    ) {
      await fs.rm(path.join(resources, entry), { recursive: true, force: true });
    }
  }

  const remaining = (await fs.readdir(resources)).sort();
  const expected = ['app.asar', 'backend-dist', 'icon.icns'];
  if (JSON.stringify(remaining) !== JSON.stringify(expected)) {
    throw new Error(`严格候选包出现未登记资源: ${remaining.join(', ')}`);
  }
};
