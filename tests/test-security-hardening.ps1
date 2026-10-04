$ErrorActionPreference = 'Stop'
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

function Get-NoRedirect {
    param([string]$Uri)
    try {
        $resp = Invoke-WebRequest -Uri $Uri -WebSession $session -MaximumRedirection 0 -UseBasicParsing
        return [PSCustomObject]@{ StatusCode = [int]$resp.StatusCode; Location = $resp.Headers.Location; Headers = $resp.Headers }
    } catch {
        $r = $_.Exception.Response
        if ($null -eq $r) { throw }
        return [PSCustomObject]@{ StatusCode = [int]$r.StatusCode; Location = $r.Headers.Location; Headers = $r.Headers }
    }
}

# 1. Security headers present even on an unauthenticated redirect.
$homeResp = Get-NoRedirect -Uri 'http://localhost:8000/'
Write-Output "GET / status: $($homeResp.StatusCode) -> $($homeResp.Location)"
Write-Output "X-Frame-Options present: $($homeResp.Headers.ContainsKey('X-Frame-Options'))"
Write-Output "CSP present: $($homeResp.Headers.ContainsKey('Content-Security-Policy'))"

# 2. Full login flow for the in-group user.
$startLogin = Get-NoRedirect -Uri 'http://localhost:8000/login/keycloak'
$authorizeUrl = $startLogin.Location
$authPage = Invoke-WebRequest -Uri $authorizeUrl -WebSession $session -UseBasicParsing
$actionMatch = [regex]::Match($authPage.Content, '<form[^>]*action="([^"]+)"')
$actionUrl = $actionMatch.Groups[1].Value -replace '&amp;', '&'
$loginPost = Invoke-WebRequest -Uri $actionUrl -Method Post -Body @{ username = 'devportal-user'; password = 'devportal-pass' } -WebSession $session -UseBasicParsing -MaximumRedirection 20
Write-Output "Post-login status: $($loginPost.StatusCode) final URL: $($loginPost.BaseResponse.ResponseUri)"

$me = Invoke-WebRequest -Uri http://localhost:8000/api/me -WebSession $session -UseBasicParsing
Write-Output "GET /api/me: $($me.Content)"

# 3. Invalid enum value should now be rejected with 422, not forwarded to GitLab.
try {
    $badPayload = @{ targetEnvironment = 'staging-hacked'; allowProd = $false; tenantList = 'tenant-a'; pipelineTrigger = 'tenant_baseline'; taint = $false } | ConvertTo-Json
    Invoke-WebRequest -Uri http://localhost:8000/api/trigger-deployment -Method Post -ContentType 'application/json' -Body $badPayload -WebSession $session -UseBasicParsing
    Write-Output "Invalid enum request unexpectedly succeeded"
} catch {
    Write-Output "Invalid enum request status: $($_.Exception.Response.StatusCode.value__)"
}

# 4. RP-initiated logout should send the browser to Keycloak's end-session endpoint.
$logout = Get-NoRedirect -Uri 'http://localhost:8000/logout'
Write-Output "GET /logout status: $($logout.StatusCode) -> $($logout.Location)"

$endSession = Invoke-WebRequest -Uri $logout.Location -WebSession $session -UseBasicParsing -MaximumRedirection 20
Write-Output "After Keycloak end-session, final URL: $($endSession.BaseResponse.ResponseUri)"
Write-Output "Landing page shows logged out: $($endSession.Content -match 'logged out')"

$meAfterLogout = Get-NoRedirect -Uri 'http://localhost:8000/api/me'
Write-Output "GET /api/me after logout status: $($meAfterLogout.StatusCode)"
