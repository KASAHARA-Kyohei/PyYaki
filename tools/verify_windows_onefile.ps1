param(
    [Parameter(Mandatory = $true)][string]$Binary,
    [Parameter(Mandatory = $true)][string]$Output,
    [ValidateRange(1, 100)][int]$Runs = 1,
    [ValidateSet('fresh', 'cached')][string]$CacheMode = 'fresh',
    [ValidateRange(20, 300)][int]$WatchdogSeconds = 120,
    [switch]$RequireCleanEnvironment,
    [switch]$RequireSignature
)

# Built-in PowerShell/.NET only. Never change execution policy, Defender, adapters or accounts.
$ErrorActionPreference = 'Stop'
$binaryPath = (Resolve-Path -LiteralPath $Binary).Path
$outputPath = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $outputPath) { throw 'Use a new output to preserve raw logs.' }
if ($outputPath -notmatch '[\\/]test-results[\\/]') { throw 'Output must be under test-results.' }
New-Item -ItemType Directory -Path $outputPath | Out-Null
$originalLocal = $env:LOCALAPPDATA
$originalModules = $env:PSModulePath
# A parent PowerShell 7 can pass a module path that omits Windows PowerShell's
# built-in modules. Add the OS module path only for this verification process.
$osModules = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\Modules'
$env:PSModulePath = "$osModules;$originalModules"
$report = [ordered]@{ status = 'running'; binary = $binaryPath; runs = @(); failures = @() }

function Save-Report {
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $outputPath 'report.json') -Encoding UTF8
}
function Environment-Evidence {
    $tools = @(@('python', 'python3', 'py', 'cargo', 'rustc', 'cl') | ForEach-Object {
        $tool = Get-Command $_ -ErrorAction SilentlyContinue
        if ($tool) { @{ name = $_; source = $tool.Source } }
    })
    $runtime = @(Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\*',
        'HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\*' -ErrorAction SilentlyContinue |
        Where-Object { $_.name -like '*WebView2*' } | Select-Object name, pv)
    $pythonRegistry = @(@('HKCU:\SOFTWARE\Python', 'HKLM:\SOFTWARE\Python',
        'HKLM:\SOFTWARE\WOW6432Node\Python') | Where-Object { Test-Path -LiteralPath $_ })
    $networkKnown = $false
    $adapters = @()
    try {
        $adapters = @(Get-NetAdapter -ErrorAction Stop | Where-Object Status -eq 'Up' |
            Select-Object Name, InterfaceDescription, Status)
        $networkKnown = $true
    } catch { $report.failures += "Network observation unavailable: $_" }
    $identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return @{ os = [Environment]::OSVersion.VersionString; tools = @($tools);
        webview2 = $runtime; pythonRegistry = @($pythonRegistry);
        admin = $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);
        networkKnown = $networkKnown; activeAdapters = $adapters }
}
function Owned-LogFiles($local, $owned, $since) {
    $base = Join-Path $local 'PyYaki\prototype'
    if (-not (Test-Path -LiteralPath $base)) { return }
    foreach ($instance in Get-ChildItem -LiteralPath $base -Directory) {
        foreach ($name in @('launcher.log', 'host.log')) {
            $path = Join-Path $instance.FullName $name
            if (-not (Test-Path -LiteralPath $path)) { continue }
            $file = Get-Item -LiteralPath $path
            if ($file.CreationTimeUtc -lt $since) { continue }
            $first = Get-Content -LiteralPath $path -Encoding UTF8 -TotalCount 1
            if ($first -notmatch '^(pyyaki_onefile|pyyaki_startup)=(.+)$') { continue }
            try { $initial = $Matches[2] | ConvertFrom-Json } catch { continue }
            if ($initial.data.pid -and $owned.ContainsKey([int]$initial.data.pid)) { $file }
        }
    }
}
function Read-Markers($local, $owned, $clock, $seen, $since) {
    foreach ($file in Owned-LogFiles $local $owned $since) {
            $path = $file.FullName
            $lines = @(Get-Content -LiteralPath $path -Encoding UTF8 -ErrorAction SilentlyContinue)
            foreach ($line in $lines) {
                if ($line -notmatch '^(pyyaki_onefile|pyyaki_startup)=(.+)$') { continue }
                try { $marker = $Matches[2] | ConvertFrom-Json } catch { continue }
                $key = "$($file.Directory.Name)/$($file.Name)/$($marker.phase)"
                if (-not $seen.Contains($key)) {
                    $seen[$key] = @{ observed_ms = $clock.Elapsed.TotalMilliseconds; record = $marker }
                }
            }
    }
}

try {
    $report.binary_sha256 = (Get-FileHash -LiteralPath $binaryPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $signature = Get-AuthenticodeSignature -LiteralPath $binaryPath
    $report.signature = $signature.Status.ToString()
    $report.environmentBefore = Environment-Evidence
    $evidence = $report.environmentBefore
    $report.cleanGate = -not $evidence.admin -and $evidence.tools.Count -eq 0 -and
        $evidence.webview2.Count -eq 0 -and $evidence.pythonRegistry.Count -eq 0 -and
        $evidence.networkKnown -and $evidence.activeAdapters.Count -eq 0
    $report.limitations = @('Marker observations are not physical T_native/T_shell/T_interactive.',
        'Profiles are per launch; cached mode reuses only the app extraction cache.',
        'Environment checks are observations, not proof of every installed component or enterprise policy.',
        'Post-reboot samples, ETW/recording and physical offline evidence remain separate.')
    if ($RequireCleanEnvironment -and -not $report.cleanGate) { throw 'Clean/offline environment gate failed.' }
    if ($RequireSignature -and $report.signature -ne 'Valid') { throw 'Authenticode trust gate failed.' }
    $count = $Runs + $(if ($CacheMode -eq 'cached') { 1 } else { 0 })
    for ($index = 0; $index -lt $count; $index++) {
        $case = Join-Path $outputPath ('run-{0:d3}' -f $index)
        New-Item -ItemType Directory -Path $case | Out-Null
        $local = if ($CacheMode -eq 'cached') { Join-Path $outputPath 'cached-local' } else { Join-Path $case 'local' }
        New-Item -ItemType Directory -Path $local -Force | Out-Null
        $env:LOCALAPPDATA = $local
        $since = [DateTime]::UtcNow
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $process = Start-Process -FilePath $binaryPath -ArgumentList '--smoke-test' -PassThru -WindowStyle Normal `
            -RedirectStandardOutput (Join-Path $case 'stdout.log') -RedirectStandardError (Join-Path $case 'stderr.log')
        $null = $process.Handle
        $owned = @{ $process.Id = $process }
        $seen = [ordered]@{}
        $samples = @()
        $watchdog = $false
        try {
            while (-not $process.HasExited) {
                $table = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId)
                do {
                    $added = $false
                    foreach ($entry in $table) {
                        if ($owned.ContainsKey([int]$entry.ParentProcessId) -and
                            -not $owned[[int]$entry.ParentProcessId].HasExited -and
                            -not $owned.ContainsKey([int]$entry.ProcessId)) {
                            try {
                                $child = Get-Process -Id $entry.ProcessId -ErrorAction Stop
                                $null = $child.Handle
                                $owned[[int]$entry.ProcessId] = $child
                                $added = $true
                            } catch { $report.failures += "Owned handle unavailable: $($entry.ProcessId)" }
                        }
                    }
                } while ($added)
                Read-Markers $local $owned $clock $seen $since
                $samples += @{ observed_ms = $clock.Elapsed.TotalMilliseconds; processes = @(
                    foreach ($entry in $owned.Values) {
                        if (-not $entry.HasExited) {
                            try {
                                $entry.Refresh()
                                @{ pid = $entry.Id; workingSet = $entry.WorkingSet64;
                                    privateBytes = $entry.PrivateMemorySize64; cpuSeconds = $entry.TotalProcessorTime.TotalSeconds }
                            } catch {
                                if (-not $entry.HasExited) { $report.failures += "Resource observation failed: $($entry.Id)" }
                            }
                        }
                    }) }
                if ($clock.Elapsed.TotalSeconds -gt $WatchdogSeconds) {
                    $watchdog = $true
                    $process.Kill() # Only this held Popen-equivalent handle; its Jobs own descendants.
                    break
                }
                Start-Sleep -Milliseconds 100
            }
            $process.WaitForExit()
            Read-Markers $local $owned $clock $seen $since
            $cleanupDeadline = [DateTime]::UtcNow.AddSeconds(5)
            while (@($owned.Values | Where-Object { -not $_.HasExited }).Count -gt 0 -and
                [DateTime]::UtcNow -lt $cleanupDeadline) { Start-Sleep -Milliseconds 100 }
            $remaining = @($owned.Values | Where-Object { -not $_.HasExited } | Select-Object -ExpandProperty Id)
            $logFiles = @(Owned-LogFiles $local $owned $since)
            $smoke = @($logFiles | Where-Object Name -eq 'host.log' |
                Get-Content -Encoding UTF8 | Where-Object { $_ -like 'native_smoke=*' })
            $ok = -not $watchdog -and $process.ExitCode -eq 0 -and $remaining.Count -eq 0 -and
                $smoke.Count -eq 1 -and ($smoke[0].Substring(13) | ConvertFrom-Json).ok -eq $true
            $record = @{ index = $index; warmup = ($CacheMode -eq 'cached' -and $index -eq 0);
                pid = $process.Id; exitCode = $process.ExitCode; watchdog = $watchdog; ok = $ok;
                remaining = $remaining; observations = $seen; samples = $samples; local = $local;
                logFiles = @($logFiles | Select-Object -ExpandProperty FullName) }
            $record | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $case 'result.json') -Encoding UTF8
            $report.runs += $record
            Save-Report
        } finally {
            if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
            foreach ($entry in $owned.Values) { $entry.Dispose() }
        }
    }
    $report.environmentAfter = Environment-Evidence
    $report.status = if (@($report.runs | Where-Object { -not $_.ok }).Count -eq 0) { 'observed' } else { 'failed' }
} catch {
    $report.status = 'failed'
    $report.failures += $_.ToString()
} finally {
    $env:LOCALAPPDATA = $originalLocal
    $env:PSModulePath = $originalModules
    Save-Report
}
if ($report.status -eq 'failed') { exit 1 }
