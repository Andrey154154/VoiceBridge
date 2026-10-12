/* Native x64 launcher / self-extracting online setup. No CRT, no admin request.
   Cross-compiled with clang + lld-link. Sources and payload are shipped. */
typedef unsigned short WCHAR;
typedef unsigned long DWORD;
typedef int BOOL;
typedef void *HANDLE;
typedef __SIZE_TYPE__ SIZE_T;
#define API __declspec(dllimport)
API DWORD __stdcall GetModuleFileNameW(HANDLE,WCHAR*,DWORD);
API DWORD __stdcall GetSystemDirectoryW(WCHAR*,DWORD);
API DWORD __stdcall GetTempPathW(DWORD,WCHAR*);
API unsigned int __stdcall GetTempFileNameW(const WCHAR*,const WCHAR*,unsigned int,WCHAR*);
API BOOL __stdcall DeleteFileW(const WCHAR*);
API BOOL __stdcall CreateDirectoryW(const WCHAR*,void*);
API BOOL __stdcall RemoveDirectoryW(const WCHAR*);
API HANDLE __stdcall CreateFileW(const WCHAR*,DWORD,DWORD,void*,DWORD,DWORD,HANDLE);
API BOOL __stdcall WriteFile(HANDLE,const void*,DWORD,DWORD*,void*);
API BOOL __stdcall CloseHandle(HANDLE);
API DWORD __stdcall WaitForSingleObject(HANDLE,DWORD);
API BOOL __stdcall GetExitCodeProcess(HANDLE,DWORD*);
API DWORD __stdcall GetPrivateProfileStringW(const WCHAR*,const WCHAR*,const WCHAR*,WCHAR*,DWORD,const WCHAR*);
API BOOL __stdcall SetEnvironmentVariableW(const WCHAR*,const WCHAR*);
API WCHAR* __stdcall GetCommandLineW(void);
API void __stdcall ExitProcess(unsigned int);
API int __stdcall MessageBoxW(HANDLE,const WCHAR*,const WCHAR*,unsigned int);

typedef struct {
    DWORD cb; WCHAR *reserved,*desktop,*title; DWORD x,y,xsize,ysize,
    xcount,ycount,fill,flags; unsigned short show,cbReserved; unsigned char *reserved2;
    HANDLE input,output,error;
} STARTUPINFO;
typedef struct {HANDLE process,thread;DWORD pid,tid;} PROCESSINFO;
API BOOL __stdcall CreateProcessW(const WCHAR*,WCHAR*,void*,void*,BOOL,DWORD,void*,const WCHAR*,STARTUPINFO*,PROCESSINFO*);
static WCHAR own[32768],root[32768],exe[32768],cmd[32768],path1[32768],path2[32768],temp[32768];
static STARTUPINFO si;static PROCESSINFO pi;
static unsigned int length(const WCHAR*s){unsigned int n=0;while(s[n])n++;return n;}
static void fail(const WCHAR*s){MessageBoxW(0,s,L"Voice Bridge",0x10);ExitProcess(1);}
static void copy(WCHAR*d,const WCHAR*s){unsigned int n=length(s);if(n>=32767)fail(L"Path too long.");for(unsigned int i=0;i<=n;i++)d[i]=s[i];}
static void append(WCHAR*d,const WCHAR*s){unsigned int a=length(d),b=length(s);if(a+b>=32767)fail(L"Command too long.");for(unsigned int i=0;i<=b;i++)d[a+i]=s[i];}
static void parent(WCHAR*p){unsigned int n=length(p);while(n && p[n-1]!='\\' && p[n-1]!='/')n--;if(n)p[n-1]=0;}
static WCHAR* extra_args(void){WCHAR*p=GetCommandLineW();if(*p=='"'){p++;while(*p && *p!='"')p++;if(*p)p++;}else{while(*p && *p!=' ' && *p!='\t')p++;}while(*p==' ' || *p=='\t')p++;return p;}
static void launch(const WCHAR*working){si.cb=sizeof(si);if(!CreateProcessW(exe,cmd,0,0,0,0x08000000,0,working,&si,&pi))fail(L"Unable to start Voice Bridge. Open setup log or reinstall the application.");CloseHandle(pi.thread);}
#ifdef SETUP
#include "payload.h"
static void write_blob(const WCHAR*path,const unsigned char*data,DWORD size){DWORD wrote=0;HANDLE f=CreateFileW(path,0x40000000,0,0,1,0x80,0);if(f==(HANDLE)-1)fail(L"Cannot create setup temporary file.");BOOL ok=WriteFile(f,data,size,&wrote,0);CloseHandle(f);if(!ok||wrote!=size)fail(L"Cannot write setup temporary file.");}
#endif
void __stdcall entry(void){
    DWORD n=GetModuleFileNameW(0,own,32768);if(!n || n>=32768)fail(L"Cannot locate application.");
    copy(root,own);parent(root);
#ifdef SETUP
    n=GetTempPathW(32768,temp);if(!n || n>=32768)fail(L"Cannot locate temporary directory.");
    if(!GetTempFileNameW(temp,L"vbs",0,path1))fail(L"Cannot reserve setup temporary path.");
    if(!DeleteFileW(path1)||!CreateDirectoryW(path1,0))fail(L"Cannot create setup directory.");
    copy(temp,path1);
    copy(path1,temp);append(path1,L"\\setup.ps1");write_blob(path1,setup_script,sizeof(setup_script));
    copy(path2,temp);append(path2,L"\\payload.zip");write_blob(path2,setup_payload,sizeof(setup_payload));
    n=GetSystemDirectoryW(exe,32768);if(!n || n>=32768)fail(L"Cannot locate system directory.");
    append(exe,L"\\WindowsPowerShell\\v1.0\\powershell.exe");
    copy(cmd,L"\"");append(cmd,exe);append(cmd,L"\" -NoProfile -STA -ExecutionPolicy Bypass -File \"");append(cmd,path1);append(cmd,L"\" -Payload \"");append(cmd,path2);append(cmd,L"\"");
    launch(temp);WaitForSingleObject(pi.process,0xffffffff);DWORD code=1;GetExitCodeProcess(pi.process,&code);CloseHandle(pi.process);
    DeleteFileW(path1);DeleteFileW(path2);
    copy(path1,temp);append(path1,L"\\result.json");DeleteFileW(path1);
    copy(path1,temp);append(path1,L"\\cancel");DeleteFileW(path1);RemoveDirectoryW(temp);
    ExitProcess(code);
#else
    copy(path1,root);append(path1,L"\\current.ini");
    if(!GetPrivateProfileStringW(L"runtime",L"python",L"",exe,32768,path1))fail(L"Voice Bridge runtime is missing. Run VoiceBridge-Setup first.");
    SetEnvironmentVariableW(L"VOICEBRIDGE_LAUNCHER",own);
    copy(cmd,L"\"");append(cmd,exe);append(cmd,L"\" -X utf8 -m voice_bridge.desktop.windows_entry ");append(cmd,extra_args());
    launch(root);CloseHandle(pi.process);ExitProcess(0);
#endif
}
