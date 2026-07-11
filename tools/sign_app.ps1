# 签名 .app 包脚本
# 默认交互输入密码；仅 -NonInteractivePassword 模式读取 METROSPEED_KEYSTORE_PASSWORD
param(
    [string]$AppPath = "",
    [string]$OutputPath = "",
    [string]$SignToolPath = "",
    [string]$JavaPath = "",
    [switch]$InteractivePassword,
    [switch]$NonInteractivePassword
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot

$buildDir = Join-Path $projectRoot "build\outputs\default"
if (-not $AppPath) { $AppPath = Join-Path $buildDir "MetroSpeed-default-unsigned.app" }
if (-not $OutputPath) { $OutputPath = Join-Path $buildDir "MetroSpeed-release.app" }

$signingDir = Join-Path $projectRoot "signing"

if ($SignToolPath) {
    $signTool = $SignToolPath
} elseif ($env:DEVECO_SDK_HOME) {
    $signTool = Join-Path $env:DEVECO_SDK_HOME "default\openharmony\toolchains\lib\hap-sign-tool.jar"
} else {
    $signTool = "C:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\lib\hap-sign-tool.jar"
}

$javaHomeTool = if ($env:JAVA_HOME) { Join-Path $env:JAVA_HOME "bin\java.exe" } else { "" }
if ($JavaPath) {
    $javaTool = $JavaPath
} elseif ($javaHomeTool -and (Test-Path -LiteralPath $javaHomeTool -PathType Leaf)) {
    $javaTool = $javaHomeTool
} elseif (Test-Path -LiteralPath "C:\Program Files\Huawei\DevEco Studio\jbr\bin\java.exe" -PathType Leaf) {
    $javaTool = "C:\Program Files\Huawei\DevEco Studio\jbr\bin\java.exe"
} else {
    $javaCommand = Get-Command java -ErrorAction SilentlyContinue
    $javaTool = if ($javaCommand) { $javaCommand.Source } else { "" }
}

$keystore = Join-Path $signingDir "release.p12"
$cert = Join-Path $signingDir "release.cer"
$profile = Join-Path $signingDir "releaseRelease.p7b"
$password = $env:METROSPEED_KEYSTORE_PASSWORD
if ($InteractivePassword -and $NonInteractivePassword) {
    throw "-InteractivePassword 与 -NonInteractivePassword 不能同时使用"
}
$useInteractivePassword = -not $NonInteractivePassword
if (-not $useInteractivePassword -and -not $password) {
    throw "非交互模式请设置环境变量 METROSPEED_KEYSTORE_PASSWORD"
}
if (-not $useInteractivePassword) {
    Write-Warning "Non-interactive mode exposes the password in Java process arguments; use -InteractivePassword on shared machines."
}
$keyAlias = "metrospeed"
$signAlg = "SHA256withECDSA"
$compatibleVersion = "12"

foreach ($requiredFile in @($AppPath, $signTool, $javaTool, $keystore, $cert, $profile)) {
    if (-not $requiredFile -or -not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "所需文件不存在: $requiredFile"
    }
}
if ([System.IO.Path]::GetFullPath($AppPath) -eq [System.IO.Path]::GetFullPath($OutputPath)) {
    throw "输出路径不能与输入 APP 相同"
}
if (Test-Path -LiteralPath $OutputPath) {
    throw "输出文件已存在，请先移走或指定新路径: $OutputPath"
}
$outputDirectory = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($OutputPath))
if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
    throw "输出目录不存在: $outputDirectory"
}

function Invoke-AppSign {
    param(
        [string]$InputFile,
        [string]$SignedFile,
        [switch]$ZipInput
    )

    $signArgs = @(
        '-jar', $signTool, 'sign-app',
        '-mode', 'localSign',
        '-keyAlias', $keyAlias,
        '-appCertFile', $cert,
        '-profileFile', $profile,
        '-inFile', $InputFile,
        '-signAlg', $signAlg,
        '-keystoreFile', $keystore,
        '-outFile', $SignedFile,
        '-compatibleVersion', $compatibleVersion,
        '-signCode', '1'
    )
    if ($useInteractivePassword) {
        $signArgs += @('-pwdInputMode', '1')
    } else {
        $signArgs += @('-keyPwd', $password, '-keystorePwd', $password)
    }
    if ($ZipInput) {
        $signArgs += @('-inForm', 'zip')
    }

    & $javaTool @signArgs
    if ($LASTEXITCODE -ne 0) {
        throw "签名失败: $InputFile"
    }
}

$tempDir = Join-Path $env:TEMP "app_sign_$(Get-Random)"
$hapUnsigned = Join-Path $tempDir "entry-default.hap"
$hapSigned = Join-Path $tempDir "entry-default-signed.hap"
$repackedApp = Join-Path $env:TEMP "app_repacked_$(Get-Random).app"
$outputFileName = [System.IO.Path]::GetFileNameWithoutExtension($OutputPath)
$signedOutputTemp = Join-Path $outputDirectory ".$outputFileName.$PID.$(Get-Random).app"

try {
    Write-Host "=== 步骤 1/4: 解压 .app 包 ===" -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($AppPath, $tempDir)
    Write-Host "解压完成: $tempDir"

    Write-Host ""
    Write-Host "=== 步骤 2/4: 给内部 HAP 签名 ===" -ForegroundColor Cyan
    Invoke-AppSign -InputFile $hapUnsigned -SignedFile $hapSigned
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
    Invoke-AppSign -InputFile $repackedApp -SignedFile $signedOutputTemp -ZipInput
    if (-not (Test-Path -LiteralPath $signedOutputTemp -PathType Leaf) -or
        (Get-Item -LiteralPath $signedOutputTemp).Length -le 0) {
        throw "签名工具未生成有效 APP"
    }
    [System.IO.File]::Move($signedOutputTemp, [System.IO.Path]::GetFullPath($OutputPath))
    Write-Host "APP 签名完成"

    Write-Host ""
    Write-Host "=== 全部完成 ===" -ForegroundColor Green
    Write-Host "输出文件: $OutputPath"
    $file = Get-Item $OutputPath
    Write-Host "文件大小: $($file.Length) 字节 ($([math]::Round($file.Length / 1KB, 1)) KB)"

} finally {
    if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
    if (Test-Path $repackedApp) { Remove-Item $repackedApp -Force }
    if (Test-Path -LiteralPath $signedOutputTemp) { Remove-Item -LiteralPath $signedOutputTemp -Force }
}
