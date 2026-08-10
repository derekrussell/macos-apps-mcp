-- mail.applescript
-- Handles all Apple Mail actions for the macos-apps-mcp server.
--
-- Called by tools/mail.py via:
--   osascript mail.applescript <action> [args...]
--
-- Actions:
--   count_messages  <unread_only> <mailbox>        -> integer
--   get_messages    <count> <offset> <unread_only> <mailbox>
--                                                  -> total\nmsg_id|subject|sender|date|is_read\n...
--   search          <mailbox> <sender> <subject> <body> <since> <until> <unread_only> <count> <offset>
--                                                  -> total\nmsg_id|subject|sender|date|is_read\n...
--   list_mailboxes                                 -> path|count\n...
--   get_body        <message_id>                   -> plain-text body
--   get_source      <message_id>                   -> raw RFC-822/MIME source
--   move            <message_id> <mailbox>         -> (no output)
--   delete          <message_id>                   -> (no output)
--   rename_mailbox  <mailbox> <new_name>           -> new account-qualified path
--   create_mailbox  <mailbox>                       -> "created|path" or "exists|path"

-- Shared handlers (sanitise_field, format_date, parse_iso_date), loaded once
-- per invocation.
property util : missing value

on run argv
    set util to load_utilities()
    set action to item 1 of argv

    if action is "count_messages" then
        set unreadOnly to item 2 of argv
        set mailboxName to item 3 of argv
        return count_messages(unreadOnly, mailboxName)
    else if action is "get_messages" then
        set batchCount to (item 2 of argv) as integer
        set batchOffset to (item 3 of argv) as integer
        set unreadOnly to item 4 of argv
        set mailboxName to item 5 of argv
        return get_messages(batchCount, batchOffset, unreadOnly, mailboxName)
    else if action is "search" then
        set mailboxName to item 2 of argv
        set senderQuery to item 3 of argv
        set subjectQuery to item 4 of argv
        set bodyQuery to item 5 of argv
        set sinceText to item 6 of argv
        set untilText to item 7 of argv
        set unreadOnly to item 8 of argv
        set batchCount to (item 9 of argv) as integer
        set batchOffset to (item 10 of argv) as integer
        return search_messages(mailboxName, senderQuery, subjectQuery, bodyQuery, sinceText, untilText, unreadOnly, batchCount, batchOffset)
    else if action is "list_mailboxes" then
        return list_mailboxes()
    else if action is "get_body" then
        return get_body(item 2 of argv)
    else if action is "get_source" then
        return get_source(item 2 of argv)
    else if action is "move" then
        move_message(item 2 of argv, item 3 of argv)
    else if action is "delete" then
        delete_message(item 2 of argv)
    else if action is "rename_mailbox" then
        return rename_mailbox(item 2 of argv, item 3 of argv)
    else if action is "create_mailbox" then
        return create_mailbox(item 2 of argv)
    else
        error "Unknown action: " & action
    end if
end run


-- Helpers
-- ------------------------------------------------------------

-- Load the shared handler library (sanitise_field, format_date, parse_iso_date)
-- that sits alongside this script. Resolved relative to this file's own path so
-- it works regardless of the caller's working directory.
on load_utilities()
    set myPosixPath to POSIX path of (path to me)
    set AppleScript's text item delimiters to "/"
    set directoryParts to items 1 thru -2 of (text items of myPosixPath)
    set utilitiesPath to (directoryParts as text) & "/utilities.applescript"
    set AppleScript's text item delimiters to ""
    return (run script (read POSIX file utilitiesPath as «class utf8»))
end load_utilities


-- Return "true"/"false" for an AppleScript boolean (the wire format's booleans).
on boolean_to_text(flag)
    if flag then
        return "true"
    else
        return "false"
    end if
end boolean_to_text


-- Build a unique, account-qualified path for a mailbox by walking up its
-- container chain to the account, e.g. "iCloud/Church/Transactions".
-- A parent mailbox reports class "container"; the account does not, which is how
-- we know when to stop climbing. Necessary because several mailboxes share a
-- leaf name (e.g. three separate "Transactions" boxes).
on mailbox_path(mailboxReference)
    tell application "Mail"
        set pathText to name of mailboxReference
        set parentContainer to container of mailboxReference
        repeat while (class of parentContainer) is container
            set pathText to (name of parentContainer) & "/" & pathText
            set parentContainer to container of parentContainer
        end repeat
        return (name of parentContainer) & "/" & pathText
    end tell
end mailbox_path


-- Resolve a mailbox from its account-qualified path (as produced by mailbox_path
-- and returned by list_mailboxes). "inbox" is accepted as a shortcut for the
-- unified inbox. "mailboxes of acct" already returns every mailbox flat,
-- including nested ones, so no recursion is needed.
on resolve_mailbox(mailboxName)
    tell application "Mail"
        if mailboxName is "inbox" then return inbox
        repeat with currentAccount in every account
            repeat with currentMailbox in (mailboxes of currentAccount)
                -- Return "contents of" the loop variable, not the loop variable
                -- itself: the latter is a positional reference (item N of
                -- mailboxes of ...) that becomes stale or resolves to the wrong
                -- mailbox once used in a later tell block.
                if (my mailbox_path(currentMailbox)) is mailboxName then return (contents of currentMailbox)
            end repeat
        end repeat
        error "Mailbox not found: " & mailboxName
    end tell
end resolve_mailbox


-- Find a message by its RFC 2822 Message-ID across every mailbox.
on find_message(messageId)
    tell application "Mail"
        repeat with currentAccount in every account
            repeat with currentMailbox in (mailboxes of currentAccount)
                try
                    return (first message of currentMailbox whose message id is messageId)
                end try
            end repeat
        end repeat
        error "Message not found: " & messageId
    end tell
end find_message


-- Format a list of messages into pipe-delimited lines, reading each message's
-- fields per-message (Mail cannot bulk-read fields off a unified-inbox
-- whose-result). Output line: message_id|subject|sender|date|is_read
-- Shared by get_messages and search_messages.
on format_message_lines(messageList)
    set output to ""
    tell application "Mail"
        repeat with currentMessage in messageList
            set messageId to message id of currentMessage
            set messageSubject to subject of currentMessage
            set messageSender to sender of currentMessage
            set messageDateText to my (util's format_date(date sent of currentMessage))
            set isReadText to my boolean_to_text(read status of currentMessage)
            set output to output & messageId & "|" & (my (util's sanitise_field(messageSubject))) & "|" & (my (util's sanitise_field(messageSender))) & "|" & messageDateText & "|" & isReadText & linefeed
        end repeat
    end tell
    return output
end format_message_lines


-- Public handlers
-- ------------------------------------------------------------

-- Return the number of messages in the named mailbox.
-- unreadOnly: "true" to count only unread messages, "false" for all.
on count_messages(unreadOnly, mailboxName)
    set targetMailbox to resolve_mailbox(mailboxName)
    tell application "Mail"
        if unreadOnly is "true" then
            return (count (messages of targetMailbox whose read status is false)) as text
        else
            return (count (messages of targetMailbox)) as text
        end if
    end tell
end count_messages


-- Return a paginated batch of messages from the named mailbox.
-- First line of output is the total message count matching the filter.
-- Subsequent lines are pipe-delimited: message_id|subject|sender|date|is_read
on get_messages(batchCount, batchOffset, unreadOnly, mailboxName)
    set targetMailbox to resolve_mailbox(mailboxName)
    tell application "Mail"
        if unreadOnly is "true" then
            set allMessages to (messages of targetMailbox whose read status is false)
        else
            set allMessages to messages of targetMailbox
        end if

        set totalCount to count of allMessages
        set output to (totalCount as text) & linefeed
        if totalCount is 0 then return output

        -- AppleScript lists are 1-indexed; batchOffset is 0-based from Python.
        set startIndex to batchOffset + 1
        if startIndex > totalCount then return output

        set endIndex to batchOffset + batchCount
        if endIndex > totalCount then set endIndex to totalCount

        set batchMessages to items startIndex thru endIndex of allMessages
        return output & (my format_message_lines(batchMessages))
    end tell
end get_messages


-- Search messages within a single mailbox by sender, subject, body, date range
-- and read state. All supplied criteria are AND-combined; empty text fields are
-- ignored. Paginated the same way as get_messages.
-- Output: total\nmsg_id|subject|sender|date|is_read\n...
--
-- Sender, subject, date range and read state are pushed into a native `whose`
-- clause so Mail returns only matches instead of us reading every message. A
-- text predicate is included ONLY when its criterion is non-empty: Mail's query
-- engine treats `contains ""` as matching NOTHING (unlike plain AppleScript), so
-- an empty field must be omitted rather than passed as a wildcard. That leaves a
-- small fixed set of clause shapes, enumerated below. The date range is always
-- present (missing bounds use wide sentinels), so the clause is never empty.
--
-- Body match is applied LOCALLY to the narrowed set: `content contains` inside a
-- `whose` would force Mail to decode every body in the mailbox, whereas reading
-- bodies only for the already-narrowed candidates is far cheaper.
on search_messages(mailboxName, senderQuery, subjectQuery, bodyQuery, sinceText, untilText, unreadOnly, batchCount, batchOffset)
    set targetMailbox to resolve_mailbox(mailboxName)

    -- Build the date bounds component-wise (ISO string coercion mangles dates).
    -- Missing bounds use sentinels; an inclusive `until` spans the whole day.
    if sinceText is "" then
        set sinceDate to util's parse_iso_date("1970-01-01")
    else
        set sinceDate to util's parse_iso_date(sinceText)
    end if
    if untilText is "" then
        set untilDate to util's parse_iso_date("2999-12-31")
    else
        set untilDate to (util's parse_iso_date(untilText)) + 1 * days - 1
    end if

    set hasSenderQuery to (senderQuery is not "")
    set hasSubjectQuery to (subjectQuery is not "")
    set unreadOnlyFlag to (unreadOnly is "true")

    tell application "Mail"
        -- Enumerate clause shapes: a predicate for sender/subject is present only
        -- when its criterion is non-empty (see note above).
        if hasSenderQuery and hasSubjectQuery then
            if unreadOnlyFlag then
                set matchedMessages to (messages of targetMailbox whose sender contains senderQuery and subject contains subjectQuery and date sent ≥ sinceDate and date sent ≤ untilDate and read status is false)
            else
                set matchedMessages to (messages of targetMailbox whose sender contains senderQuery and subject contains subjectQuery and date sent ≥ sinceDate and date sent ≤ untilDate)
            end if
        else if hasSenderQuery then
            if unreadOnlyFlag then
                set matchedMessages to (messages of targetMailbox whose sender contains senderQuery and date sent ≥ sinceDate and date sent ≤ untilDate and read status is false)
            else
                set matchedMessages to (messages of targetMailbox whose sender contains senderQuery and date sent ≥ sinceDate and date sent ≤ untilDate)
            end if
        else if hasSubjectQuery then
            if unreadOnlyFlag then
                set matchedMessages to (messages of targetMailbox whose subject contains subjectQuery and date sent ≥ sinceDate and date sent ≤ untilDate and read status is false)
            else
                set matchedMessages to (messages of targetMailbox whose subject contains subjectQuery and date sent ≥ sinceDate and date sent ≤ untilDate)
            end if
        else
            if unreadOnlyFlag then
                set matchedMessages to (messages of targetMailbox whose date sent ≥ sinceDate and date sent ≤ untilDate and read status is false)
            else
                set matchedMessages to (messages of targetMailbox whose date sent ≥ sinceDate and date sent ≤ untilDate)
            end if
        end if

        -- Body match: filter the narrowed candidates locally, reading content
        -- only for them. Messages whose body cannot be read are skipped.
        if bodyQuery is not "" then
            set bodyMatchedMessages to {}
            repeat with currentMessage in matchedMessages
                try
                    ignoring case
                        if (content of currentMessage) contains bodyQuery then set end of bodyMatchedMessages to (contents of currentMessage)
                    end ignoring
                end try
            end repeat
        else
            set bodyMatchedMessages to matchedMessages
        end if

        set totalCount to count of bodyMatchedMessages
        set output to (totalCount as text) & linefeed
        if totalCount is 0 then return output

        -- AppleScript lists are 1-indexed; batchOffset is 0-based from Python.
        set startIndex to batchOffset + 1
        if startIndex > totalCount then return output
        set endIndex to batchOffset + batchCount
        if endIndex > totalCount then set endIndex to totalCount

        set batchMessages to items startIndex thru endIndex of bodyMatchedMessages
        return output & (my format_message_lines(batchMessages))
    end tell
end search_messages


-- Return all mailboxes across all accounts with their message counts.
-- Output is pipe-delimited: path|count, where path is account-qualified
-- (e.g. "iCloud/Church/Transactions"). "mailboxes of acct" already returns every
-- mailbox flat, so each appears exactly once with no recursion.
on list_mailboxes()
    tell application "Mail"
        set output to ""
        repeat with currentAccount in every account
            repeat with currentMailbox in (mailboxes of currentAccount)
                set output to output & (my mailbox_path(currentMailbox)) & "|" & ((count of messages of currentMailbox) as text) & linefeed
            end repeat
        end repeat
        return output
    end tell
end list_mailboxes


-- Return the plain-text body of the message with the given RFC 2822 Message-ID.
on get_body(messageId)
    set targetMessage to find_message(messageId)
    tell application "Mail"
        return content of targetMessage
    end tell
end get_body


-- Return the raw RFC-822/MIME source of the message with the given Message-ID.
-- Unlike `content` (Mail's plain-text rendering), `source` preserves the full
-- MIME structure including the text/html part, so the caller can parse out the
-- linked <img> URLs in Python. The whole result is the blob (not pipe records),
-- so it is returned as-is; the message must be downloaded and source can be large.
on get_source(messageId)
    set targetMessage to find_message(messageId)
    tell application "Mail"
        return source of targetMessage
    end tell
end get_source


-- Move the message with the given Message-ID to the named mailbox.
on move_message(messageId, mailboxName)
    set targetMessage to find_message(messageId)
    set targetMailbox to resolve_mailbox(mailboxName)
    tell application "Mail"
        move targetMessage to targetMailbox
    end tell
end move_message


-- Move the message with the given Message-ID to the Trash.
on delete_message(messageId)
    set targetMessage to find_message(messageId)
    tell application "Mail"
        delete targetMessage
    end tell
end delete_message


-- Rename a mailbox (change its leaf name; it stays in the same account/parent).
-- Returns the new account-qualified path.
--
-- Mail's AppleScript interface cannot DELETE a mailbox - `delete` fails with a
-- generic "-10000" for every mailbox type (local, POP, IMAP/iCloud), a
-- long-standing limitation across macOS versions. Renaming DOES work, so it is
-- the supported way to flag a mailbox for manual deletion in Mail.app.
on rename_mailbox(mailboxName, newName)
    set targetMailbox to resolve_mailbox(mailboxName)
    tell application "Mail"
        set name of targetMailbox to newName
    end tell
    -- After renaming, `targetMailbox` is a stale by-name specifier (its old name
    -- no longer resolves - re-reading it throws -1728, errAENoSuchObject), so
    -- don't re-read it. A rename keeps the mailbox in the same parent, so the new
    -- path is the input path's parent + the new leaf.
    set AppleScript's text item delimiters to "/"
    set pathParts to text items of mailboxName
    if (count of pathParts) > 1 then
        set parentPath to (items 1 thru -2 of pathParts) as text
        set newPath to parentPath & "/" & newName
    else
        set newPath to newName
    end if
    set AppleScript's text item delimiters to ""
    return newPath
end rename_mailbox


-- Create a mailbox under an existing account, given an account-qualified path.
-- The first path segment is the account name; the rest is the mailbox to create.
-- Output: "created|<path>" when made, or "exists|<path>" when already present.
--
-- Creation is via `make new mailbox at end of mailboxes of <account> with
-- properties {name:"<within-account path>"}`. (Nesting via `at end of mailboxes
-- of <parent mailbox>` fails -10000, and a bare `make new mailbox {name:"acct/x"}`
-- creates a LOCAL mailbox - this account-targeted form is the one that works.)
--
-- Each level of a nested path is created EXPLICITLY, top-down, skipping levels
-- that already exist. Passing a full nested path to a single `make` would let
-- Mail auto-create the missing intermediates as IMAP \NoSelect containers -
-- they hold children but not messages, don't appear in mail_list_mailboxes, and
-- can't be a move destination or resolved by path. Creating each level while its
-- parent already exists keeps every level a real, selectable mailbox.
on create_mailbox(mailboxName)
    -- Split the account (first segment) from the within-account segments.
    set AppleScript's text item delimiters to "/"
    set pathParts to text items of mailboxName
    if (count of pathParts) < 2 then
        set AppleScript's text item delimiters to ""
        error "mailbox must be an account-qualified path, e.g. 'iCloud/Receipts'"
    end if
    set accountName to item 1 of pathParts
    set withinAccountSegments to items 2 thru -1 of pathParts
    set AppleScript's text item delimiters to ""

    -- Idempotent: if the full path already exists, report it unchanged.
    try
        set existingMailbox to resolve_mailbox(mailboxName)
        return "exists|" & (my mailbox_path(existingMailbox))
    end try

    tell application "Mail"
        if accountName is not in (name of every account) then
            error "No such account '" & accountName & "'. Use the account name shown by mail_list_mailboxes (the first path segment)."
        end if
    end tell

    -- Create each cumulative level explicitly, top-down, skipping existing ones.
    set cumulativePath to ""
    repeat with segmentIndex from 1 to (count of withinAccountSegments)
        if segmentIndex is 1 then
            set cumulativePath to item segmentIndex of withinAccountSegments
        else
            set cumulativePath to cumulativePath & "/" & (item segmentIndex of withinAccountSegments)
        end if

        set levelExists to false
        try
            resolve_mailbox(accountName & "/" & cumulativePath)
            set levelExists to true
        end try
        if not levelExists then
            tell application "Mail"
                make new mailbox at end of mailboxes of account accountName with properties {name:cumulativePath}
            end tell
            delay 1
        end if
    end repeat

    -- Confirm and return the canonical path of the created mailbox.
    set createdMailbox to resolve_mailbox(mailboxName)
    return "created|" & (my mailbox_path(createdMailbox))
end create_mailbox
