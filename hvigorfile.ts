import { appTasks } from '@ohos/hvigor-ohos-plugin';
import * as fs from 'fs';

const LOCK_STALE_MS = 5 * 60 * 1000;
const lockPath = __dirname + '/.sync-version.lock';

function replaceExactlyOnce(text: string, pattern: RegExp, replacement: string, label: string): string {
    const matches = text.match(new RegExp(pattern.source, 'g')) || [];
    if (matches.length !== 1) {
        throw new Error(`Expected one ${label}, found ${matches.length}`);
    }
    return text.replace(pattern, replacement);
}

function atomicWrite(path: string, text: string, expectedText: string): void {
    if (fs.readFileSync(path, 'utf-8') !== expectedText) {
        throw new Error(`Concurrent change detected before writing ${path}`);
    }
    const tempPath = `${path}.${process.pid}.${Date.now()}.tmp`;
    let tempFd: number | undefined;
    try {
        tempFd = fs.openSync(tempPath, 'wx');
        fs.writeFileSync(tempFd, text, 'utf-8');
        fs.fsyncSync(tempFd);
        fs.closeSync(tempFd);
        tempFd = undefined;
        if (fs.readFileSync(path, 'utf-8') !== expectedText) {
            throw new Error(`Concurrent change detected while writing ${path}`);
        }
        fs.renameSync(tempPath, path);
    } finally {
        if (tempFd !== undefined) {
            fs.closeSync(tempFd);
        }
        if (fs.existsSync(tempPath)) {
            fs.unlinkSync(tempPath);
        }
    }
}

function acquireVersionLock(): { fd: number; token: string } {
    const token = `pid=${process.pid} time=${Date.now()}\n`;
    for (let attempt = 0; attempt < 2; attempt++) {
        try {
            const fd = fs.openSync(lockPath, 'wx');
            fs.writeFileSync(fd, token, 'ascii');
            fs.fsyncSync(fd);
            return { fd, token };
        } catch (error) {
            const errorCode = (error as { code?: string }).code;
            if (errorCode !== 'EEXIST') {
                throw error;
            }
            try {
                if (Date.now() - fs.statSync(lockPath).mtimeMs > LOCK_STALE_MS) {
                    fs.unlinkSync(lockPath);
                    continue;
                }
            } catch (staleError) {
                const staleCode = (staleError as { code?: string }).code;
                if (staleCode === 'ENOENT') {
                    continue;
                }
                throw staleError;
            }
            throw new Error(`Another build/version sync is running: ${lockPath}`);
        }
    }
    throw new Error(`Could not acquire version lock: ${lockPath}`);
}

function releaseVersionLock(fd: number, token: string): void {
    fs.closeSync(fd);
    try {
        if (fs.readFileSync(lockPath, 'ascii') === token) {
            fs.unlinkSync(lockPath);
        }
    } catch (error) {
        if ((error as { code?: string }).code !== 'ENOENT') {
            throw error;
        }
    }
}

function updateBuildVersion(): void {
    const lock = acquireVersionLock();
    try {
        const appJson5Path = __dirname + '/AppScope/app.json5';
        const recorderPath = __dirname + '/entry/src/main/ets/model/ResearchRecorder.ets';
        const appText = fs.readFileSync(appJson5Path, 'utf-8');
        const recorderText = fs.readFileSync(recorderPath, 'utf-8');
        const appCodeMatches = appText.match(/"versionCode"\s*:\s*(\d+)/g) || [];
        const recorderCodeMatches = recorderText.match(/const APP_VERSION_CODE\s*=\s*(\d+);/g) || [];
        const versionNameMatches = appText.match(/"versionName"\s*:\s*"([^"]+)"/g) || [];
        if (appCodeMatches.length !== 1 || recorderCodeMatches.length !== 1 || versionNameMatches.length !== 1) {
            throw new Error('Version fields must each occur exactly once');
        }

        const currentAppCode = Number(appCodeMatches[0].match(/\d+/)![0]);
        const currentRecorderCode = Number(recorderCodeMatches[0].match(/\d+/)![0]);
        const versionName = versionNameMatches[0].match(/"([^"]+)"\s*$/)![1];
        const versionCode = Math.max(
            Math.floor(Date.now() / 1000),
            currentAppCode + 1,
            currentRecorderCode + 1
        );
        if (!Number.isSafeInteger(versionCode) || versionCode > 2147483647) {
            throw new Error(`Invalid generated versionCode: ${versionCode}`);
        }

        const appUpdated = replaceExactlyOnce(
            appText,
            /"versionCode"\s*:\s*\d+/,
            `"versionCode": ${versionCode}`,
            'versionCode'
        );
        const recorderUpdated = replaceExactlyOnce(
            recorderText,
            /const APP_VERSION_CODE\s*=\s*\d+;/,
            `const APP_VERSION_CODE = ${versionCode};`,
            'APP_VERSION_CODE'
        );

        const written: Array<{ path: string; staged: string; original: string }> = [];
        try {
            atomicWrite(appJson5Path, appUpdated, appText);
            written.push({ path: appJson5Path, staged: appUpdated, original: appText });
            atomicWrite(recorderPath, recorderUpdated, recorderText);
            written.push({ path: recorderPath, staged: recorderUpdated, original: recorderText });
        } catch (error) {
            const rollbackErrors: string[] = [];
            for (let i = written.length - 1; i >= 0; i--) {
                const item = written[i];
                try {
                    atomicWrite(item.path, item.original, item.staged);
                } catch (rollbackError) {
                    rollbackErrors.push(String(rollbackError));
                }
            }
            if (rollbackErrors.length > 0) {
                throw new Error(`Version update failed; rollback incomplete: ${rollbackErrors.join('; ')}`);
            }
            throw error;
        }

        console.log(`[metro-speed] version ${versionCode} / ${versionName}`);
    } finally {
        releaseVersionLock(lock.fd, lock.token);
    }
}

updateBuildVersion();

export default {
    system: appTasks
};
