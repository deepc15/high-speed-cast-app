@echo off
setlocal
set "JAVA_HOME=C:\Program Files\Java\jdk-21"
set "GRADLE_BAT=%USERPROFILE%\.gradle\wrapper\dists\gradle-8.7-bin\bhs2wmbdwecv87pi65oeuq5iu\gradle-8.7\bin\gradle.bat"
if exist "%GRADLE_BAT%" (
    call "%GRADLE_BAT%" assembleDebug %*
) else (
    gradle assembleDebug %*
)
