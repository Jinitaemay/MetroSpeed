# 签名 .app 包脚本
# 使用前设置环境变量 METROSPEED_KEYSTORE_PASSWORD
param(
    [string]$AppPath = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot

$buildDir = Join-Path $projectRoot "build\outputs\default"
if (-not $AppPath) { $AppPath = Join-Path $buildDir "MetroSpeed-default-unsigned.app" }
if (-not $OutputPath) { $OutputPath = Join-Path $buildDir "MetroSpeed-release.app" }

$signingDir = Join-Path $projectRoot "signing"
$signTool = "C:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\lib\hap-sign-tool.jar"
$keystore = Join-Path $signingDir "release.p12"
$cert = Join-Path $signingDir "release.cer"
$profile = Join-Path $signingDir "releaseRelease.p7b"
$password = $env:METROSPEED_KEYSTORE_PASSWORD
if (-not $password) { throw "请设置环境变量 METROSPEED_KEYSTORE_PASSWORD" }
$keyAlias = "metrospeed"
$signAlg = "SHA256withECDSA"
$compatibleVersion = "12"

$tempDir = Join-Path $env:TEMP "app_sign_$(Get-Random)"
$hapUnsigned = Join-Path $tempDir "entry-default.hap"
$hapSigned = Join-Path $tempDir "entry-default-signed.hap"
$repackedApp = Join-Path $env:TEMP "app_repacked_$(Get-Random).app"

try {
    Write-Host "=== 步骤 1/4: 解压 .app 包 ===" -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($AppPath, $tempDir)
    Write-Host "解压完成: $tempDir"

    Write-Host ""
    Write-Host "=== 步骤 2/4: 给内部 HAP 签名 ===" -ForegroundColor Cyan
    & java -jar $signTool sign-app `
        -mode localSign `
        -keyAlias $keyAlias `
        -keyPwd $password `
        -appCertFile $cert `
        -profileFile $profile `
        -inFile $hapUnsigned `
        -signAlg $signAlg `
        -keystoreFile $keystore `
        -keystorePwd $password `
        -outFile $hapSigned `
        -compatibleVersion $compatibleVersion `
        -signCode "1"

    if ($LASTEXITCODE -ne 0) { throw "HAP 签名失败" }
    Write-Host "HAP 签名完成"

    Remove-Item $hapUnsigned
    Rename-Item $hapSigned "entry-default.hap"

    Write-Host ""
    Write-Host "=== 步骤 3/4: 重新打包 .app ===" -ForegroundColor Cyan
    if (Test-Path $repackedApp) { Remove-Item $repackedApp }
    [System.IO.Compression.ZipFile]::CreateFromDirectory($tempDir, $repackedApp)
    Write-Host "重新打包完成"

    Write-Host ""
    Write-Host "=== 步骤 4/4: 给 .app 包签名 ===" -ForegroundColor Cyan
    & java -jar $signTool sign-app `
        -mode localSign `
        -keyAlias $keyAlias `
        -keyPwd $password `
        -appCertFile $cert `
        -profileFile $profile `
        -inFile $repackedApp `
        -signAlg $signAlg `
        -keystoreFile $keystore `
        -keystorePwd $password `
        -outFile $OutputPath `
        -compatibleVersion $compatibleVersion `
        -signCode "1" `
        -inForm zip

    if ($LASTEXITCODE -ne 0) { throw "APP 签名失败" }
    Write-Host "APP 签名完成"

    Write-Host ""
    Write-Host "=== 全部完成 ===" -ForegroundColor Green
    Write-Host "输出文件: $OutputPath"
    $file = Get-Item $OutputPath
    Write-Host "文件大小: $($file.Length) 字节 ($([math]::Round($file.Length / 1KB, 1)) KB)"

} finally {
    if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
    if (Test-Path $repackedApp) { Remove-Item $repackedApp -Force }
}
