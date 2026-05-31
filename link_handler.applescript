-- inboxclone:// scheme handler for the native (pywebview) Inbox app.
-- Ensures the desktop app is running, then asks its window to surface the thread
-- via POST /api/open_thread (the SPA picks it up over SSE). Falls back to Gmail.
property appPath : (POSIX path of (path to home folder)) & "Desktop/Apps/Inbox.app"
property serverURL : "http://127.0.0.1:5008"

on serverUp()
	try
		do shell script "curl -sf " & serverURL & "/api/health -o /dev/null"
		return true
	on error
		return false
	end try
end serverUp

on launchApp()
	do shell script "open " & quoted form of appPath
end launchApp

on run
	launchApp()
end run

on open location this_URL
	set threadId to ""
	set gmailURL to ""
	try
		set afterScheme to text ((offset of "thread/" in this_URL) + 7) thru -1 of this_URL
		if afterScheme contains "?" then
			set threadId to text 1 thru ((offset of "?" in afterScheme) - 1) of afterScheme
		else
			set threadId to afterScheme
		end if
	end try
	try
		if this_URL contains "gmail=" then
			set rawGmail to text ((offset of "gmail=" in this_URL) + 6) thru -1 of this_URL
			set gmailURL to do shell script "python3 -c 'import urllib.parse,sys;print(urllib.parse.unquote(sys.argv[1]))' " & quoted form of rawGmail
		end if
	end try
	if not serverUp() then
		launchApp()
		repeat 120 times
			delay 0.5
			if serverUp() then exit repeat
		end repeat
	end if
	if serverUp() and threadId is not "" then
		do shell script "curl -s -X POST " & serverURL & "/api/open_thread -H 'Content-Type: application/json' -d " & quoted form of ("{\"threadId\":\"" & threadId & "\"}")
		launchApp()
	else if gmailURL is not "" then
		do shell script "open " & quoted form of gmailURL
	end if
end open location
