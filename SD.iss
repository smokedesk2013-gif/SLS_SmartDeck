[Setup]
; 프로그램 기본 정보
AppName=SLS Smart Deck
AppVersion=1.0
SetupIconFile=Setup.ico
AppPublisher=Arirang TV Ai Media R&D

; 기본 설치 경로 설정 (Program Files 폴더)
DefaultDirName={autopf}\SLS Smart Deck
DefaultGroupName=SLS Smart Deck

; 설치 파일이 생성될 위치와 이름
OutputDir=.\Output
OutputBaseFilename=SLS_SmartDeck_Setup_v1.0
Compression=lzma
SolidCompression=yes

; 권한 설정
PrivilegesRequired=admin

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 실제 포함될 파일 (경로가 다를 경우 dist 폴더의 절대 경로로 수정하세요)
Source: "dist\RecRouter_260910_GM_02.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 시작 메뉴 및 바탕화면 바로가기 생성
Name: "{group}\SLS Smart Deck"; Filename: "{app}\RecRouter_260910_GM_02.exe"
Name: "{autodesktop}\SLS Smart Deck"; Filename: "{app}\RecRouter_260910_GM_02.exe"; Tasks: desktopicon

[Run]
; 설치 완료 후 프로그램 자동 실행
Filename: "{app}\RecRouter_260910_GM_02.exe"; Description: "{cm:LaunchProgram,SLS Smart Deck}"; Flags: nowait postinstall skipifsilent