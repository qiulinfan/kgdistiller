[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

& kgdistiller claude link @Arguments
exit $LASTEXITCODE
