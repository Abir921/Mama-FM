# Starts the Lavalink music server.
# Lavalink needs Java 17+. On this machine `java` on PATH is a Java 8 JRE,
# so we resolve a suitable JDK explicitly instead of relying on PATH.

$ErrorActionPreference = "Stop"

function Get-JavaMajor {
    # Read the JDK's `release` file (JAVA_VERSION="25.0.1") rather than running
    # `java -version`: that writes to stderr, and in PowerShell 5.1 capturing
    # native stderr raises a terminating NativeCommandError.
    param([string]$JavaHome)

    $releaseFile = Join-Path $JavaHome "release"
    if (-not (Test-Path $releaseFile)) { return 0 }

    $line = Select-String -Path $releaseFile -Pattern '^JAVA_VERSION="?([0-9]+)' | Select-Object -First 1
    if (-not $line) { return 0 }
    return [int]$line.Matches[0].Groups[1].Value
}

$candidates = @(
    $env:JAVA_HOME,
    "C:\Program Files\Java\jdk-25",
    "C:\Program Files\Java\latest"
) | Where-Object { $_ } | Select-Object -Unique

$java = $null
foreach ($javaHome in $candidates) {
    $exe = Join-Path $javaHome "bin\java.exe"
    if ((Test-Path $exe) -and ((Get-JavaMajor $javaHome) -ge 17)) {
        $java = $exe
        break
    }
}

if (-not $java) {
    Write-Error "No Java 17+ found. Install a JDK 17 or newer, then re-run."
    exit 1
}

Write-Host "Using: $java"
Set-Location (Join-Path $PSScriptRoot "lavalink")

if (-not (Test-Path "Lavalink.jar")) {
    Write-Error "Lavalink.jar missing from $(Get-Location). Download it from https://github.com/lavalink-devs/Lavalink/releases"
    exit 1
}

& $java -jar Lavalink.jar
