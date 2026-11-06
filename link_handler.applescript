-- inboxclone:// and mailto: handler for the native (pywebview) Inbox app.
-- Ensures the desktop app is running, then asks its window to surface the thread
-- via POST /api/open_thread, or to open a prefilled compose via POST /api/compose
-- (the SPA picks both up over SSE). A thread falls back to Gmail.
-- Build and register with setup_link_handler.sh.
property appPath : "/Applications/Inbox.app"
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

on waitForServer()
	if not serverUp() then
		launchApp()
		repeat 120 times
			delay 0.5
			if serverUp() then exit repeat
		end repeat
	end if
	return serverUp()
end waitForServer

on openMailto(this_URL)
	if waitForServer() then
		do shell script "curl -s -X POST " & serverURL & "/api/compose --data-urlencode " & quoted form of ("url=" & this_URL)
		launchApp()
	else
		-- No local server: hand the link to Gmail's own compose handler.
		do shell script "open " & quoted form of ("https://mail.google.com/mail/?extsrc=mailto&url=" & (do shell script "python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1], safe=\"\"))' " & quoted form of this_URL))
	end if
end openMailto

on open location this_URL
	if this_URL starts with "mailto:" or this_URL starts with "MAILTO:" then
		openMailto(this_URL)
		return
	end if
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
	if threadId is "" then
		-- Bare inboxclone:// (e.g. clicking a burst-summary notification):
		-- just open/raise the app.
		launchApp()
		return
	end if
	try
		if this_URL contains "gmail=" then
			set rawGmail to text ((offset of "gmail=" in this_URL) + 6) thru -1 of this_URL
			set gmailURL to do shell script "python3 -c 'import urllib.parse,sys;print(urllib.parse.unquote(sys.argv[1]))' " & quoted form of rawGmail
		end if
	end try
	if waitForServer() and threadId is not "" then
		do shell script "curl -s -X POST " & serverURL & "/api/open_thread -H 'Content-Type: application/json' -d " & quoted form of ("{\"threadId\":\"" & threadId & "\"}")
		launchApp()
	else if gmailURL is not "" then
		do shell script "open " & quoted form of gmailURL
	end if
end open location
