import { appTasks } from '@ohos/hvigor-ohos-plugin';
import * as fs from 'fs';

function updateBuildVersion(): void {
    const now = new Date();
    const versionCode = Math.floor(now.getTime() / 1000);

    const appJson5Path = __dirname + '/AppScope/app.json5';
    const appText = fs.readFileSync(appJson5Path, 'utf-8');
    const versionNameMatch = appText.match(/"versionName"\s*:\s*"([^"]+)"/);
    const versionName = versionNameMatch ? versionNameMatch[1] : 'unknown';
    const appUpdated = appText.replace(/"versionCode"\s*:\s*\d+/, `"versionCode": ${versionCode}`);
    fs.writeFileSync(appJson5Path, appUpdated, 'utf-8');

    const recorderPath = __dirname + '/entry/src/main/ets/model/ResearchRecorder.ets';
    const recorderText = fs.readFileSync(recorderPath, 'utf-8');
    const recorderUpdated = recorderText.replace(
        /const APP_VERSION_CODE\s*=\s*\d+;/,
        `const APP_VERSION_CODE = ${versionCode};`
    );
    fs.writeFileSync(recorderPath, recorderUpdated, 'utf-8');

    console.log(`[metro-speed] version ${versionCode} / ${versionName}`);
}

updateBuildVersion();

export default {
    system: appTasks
};
