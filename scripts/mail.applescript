-- mail.applescript
-- Handles all Apple Mail actions for the apple-mcp server.
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
--   list_mailboxes                                 -> name|count\n...
--   get_body        <message_id>                   -> plain-text body
--   move            <message_id> <mailbox>         -> (no output)
--   delete          <message_id>                   -> (no output)
--   delete_mailbox  <mailbox>                      -> (no output)

-- Shared handlers (sanitise_field, format_date), loaded once per invocation.
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
        set senderQ to item 3 of argv
        set subjectQ to item 4 of argv
        set bodyQ to item 5 of argv
        set sinceStr to item 6 of argv
        set untilStr to item 7 of argv
        set unreadOnly to item 8 of argv
        set batchCount to (item 9 of argv) as integer
        set batchOffset to (item 10 of argv) as integer
        return search_messages(mailboxName, senderQ, subjectQ, bodyQ, sinceStr, untilStr, unreadOnly, batchCount, batchOffset)
    else if action is "list_mailboxes" then
        return list_mailboxes()
    else if action is "get_body" then
        return get_body(item 2 of argv)
    else if action is "move" then
        move_message(item 2 of argv, item 3 of argv)
    else if action is "delete" then
        delete_message(item 2 of argv)
    else if action is "delete_mailbox" then
        delete_mailbox(item 2 of argv)
    else
        error "Unknown action: " & action
    end if
end run


-- Utilities
-- ------------------------------------------------------------

-- Load the shared handler library (sanitise_field, format_date) that sits
-- alongside this script. Resolved relative to this file's own path so it
-- works regardless of the caller's working directory.
on load_utilities()
    set myPosix to POSIX path of (path to me)
    set AppleScript's text item delimiters to "/"
    set dirParts to items 1 thru -2 of (text items of myPosix)
    set utilPath to (dirParts as text) & "/utilities.applescript"
    set AppleScript's text item delimiters to ""
    return (run script (read POSIX file utilPath as «class utf8»))
end load_utilities


-- Build a unique, account-qualified path for a mailbox by walking up its
-- container chain to the account, e.g. "iCloud/Church/Transactions".
-- A parent mailbox reports class "container"; the account does not, which
-- is how we know when to stop climbing. Necessary because several mailboxes
-- share a leaf name (e.g. three separate "Transactions" boxes).
on mailbox_path(mbx)
    tell application "Mail"
        set pathStr to name of mbx
        set c to container of mbx
        repeat while (class of c) is container
            set pathStr to (name of c) & "/" & pathStr
            set c to container of c
        end repeat
        return (name of c) & "/" & pathStr
    end tell
end mailbox_path


-- Resolve a mailbox from its account-qualified path (as produced by
-- mailbox_path and returned by list_mailboxes). "inbox" is accepted as a
-- shortcut for the unified inbox. "mailboxes of acct" already returns every
-- mailbox flat, including nested ones, so no recursion is needed.
on resolve_mailbox(mailboxName)
    tell application "Mail"
        if mailboxName is "inbox" then return inbox
        repeat with acct in every account
            repeat with mbx in (mailboxes of acct)
                -- Return "contents of" the loop variable, not the loop
                -- variable itself: the latter is a positional reference
                -- (item N of mailboxes of ...) that becomes stale or resolves
                -- to the wrong mailbox once used in a later tell block.
                if (my mailbox_path(mbx)) is mailboxName then return (contents of mbx)
            end repeat
        end repeat
        error "Mailbox not found: " & mailboxName
    end tell
end resolve_mailbox


-- Find a message by its RFC 2822 Message-ID across every mailbox.
on find_message(messageId)
    tell application "Mail"
        repeat with acct in every account
            repeat with mbx in (mailboxes of acct)
                try
                    return (first message of mbx whose message id is messageId)
                end try
            end repeat
        end repeat
        error "Message not found: " & messageId
    end tell
end find_message


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
        set startIdx to batchOffset + 1
        if startIdx > totalCount then return output

        set endIdx to batchOffset + batchCount
        if endIdx > totalCount then set endIdx to totalCount

        set batchMessages to items startIdx thru endIdx of allMessages

        repeat with msg in batchMessages
            set msgId to message id of msg
            set msgSubject to subject of msg
            set msgSender to sender of msg
            set msgDate to my (util's format_date(date sent of msg))
            if read status of msg is true then
                set msgRead to "true"
            else
                set msgRead to "false"
            end if
            set output to output & msgId & "|" & (my (util's sanitise_field(msgSubject))) & "|" & (my (util's sanitise_field(msgSender))) & "|" & msgDate & "|" & msgRead & linefeed
        end repeat

        return output
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
on search_messages(mailboxName, senderQ, subjectQ, bodyQ, sinceStr, untilStr, unreadOnly, batchCount, batchOffset)
    set targetMailbox to resolve_mailbox(mailboxName)

    -- Build the date bounds component-wise (ISO string coercion mangles dates).
    -- Missing bounds use sentinels; an inclusive `until` spans the whole day.
    if sinceStr is "" then
        set sinceDate to util's parse_iso_date("1970-01-01")
    else
        set sinceDate to util's parse_iso_date(sinceStr)
    end if
    if untilStr is "" then
        set untilDate to util's parse_iso_date("2999-12-31")
    else
        set untilDate to (util's parse_iso_date(untilStr)) + 1 * days - 1
    end if

    set hasSender to (senderQ is not "")
    set hasSubject to (subjectQ is not "")
    set unread to (unreadOnly is "true")

    tell application "Mail"
        -- Enumerate clause shapes: a predicate for sender/subject is present
        -- only when its criterion is non-empty (see note above).
        if hasSender and hasSubject then
            if unread then
                set matched to (messages of targetMailbox whose sender contains senderQ and subject contains subjectQ and date sent ≥ sinceDate and date sent ≤ untilDate and read status is false)
            else
                set matched to (messages of targetMailbox whose sender contains senderQ and subject contains subjectQ and date sent ≥ sinceDate and date sent ≤ untilDate)
            end if
        else if hasSender then
            if unread then
                set matched to (messages of targetMailbox whose sender contains senderQ and date sent ≥ sinceDate and date sent ≤ untilDate and read status is false)
            else
                set matched to (messages of targetMailbox whose sender contains senderQ and date sent ≥ sinceDate and date sent ≤ untilDate)
            end if
        else if hasSubject then
            if unread then
                set matched to (messages of targetMailbox whose subject contains subjectQ and date sent ≥ sinceDate and date sent ≤ untilDate and read status is false)
            else
                set matched to (messages of targetMailbox whose subject contains subjectQ and date sent ≥ sinceDate and date sent ≤ untilDate)
            end if
        else
            if unread then
                set matched to (messages of targetMailbox whose date sent ≥ sinceDate and date sent ≤ untilDate and read status is false)
            else
                set matched to (messages of targetMailbox whose date sent ≥ sinceDate and date sent ≤ untilDate)
            end if
        end if

        -- Body match: filter the narrowed candidates locally, reading content
        -- only for them. Messages whose body cannot be read are skipped.
        if bodyQ is not "" then
            set filtered to {}
            repeat with msg in matched
                try
                    ignoring case
                        if (content of msg) contains bodyQ then set end of filtered to (contents of msg)
                    end ignoring
                end try
            end repeat
        else
            set filtered to matched
        end if

        set totalCount to count of filtered
        set output to (totalCount as text) & linefeed
        if totalCount is 0 then return output

        -- AppleScript lists are 1-indexed; batchOffset is 0-based from Python.
        set startIdx to batchOffset + 1
        if startIdx > totalCount then return output
        set endIdx to batchOffset + batchCount
        if endIdx > totalCount then set endIdx to totalCount

        set batchMessages to items startIdx thru endIdx of filtered

        repeat with msg in batchMessages
            set msgId to message id of msg
            set msgSubject to subject of msg
            set msgSender to sender of msg
            set msgDate to my (util's format_date(date sent of msg))
            if read status of msg is true then
                set msgRead to "true"
            else
                set msgRead to "false"
            end if
            set output to output & msgId & "|" & (my (util's sanitise_field(msgSubject))) & "|" & (my (util's sanitise_field(msgSender))) & "|" & msgDate & "|" & msgRead & linefeed
        end repeat

        return output
    end tell
end search_messages


-- Return all mailboxes across all accounts with their message counts.
-- Output is pipe-delimited: path|count, where path is account-qualified
-- (e.g. "iCloud/Church/Transactions"). "mailboxes of acct" already returns
-- every mailbox flat, so each appears exactly once with no recursion.
on list_mailboxes()
    tell application "Mail"
        set output to ""
        repeat with acct in every account
            repeat with mbx in (mailboxes of acct)
                set output to output & (my mailbox_path(mbx)) & "|" & ((count of messages of mbx) as text) & linefeed
            end repeat
        end repeat
        return output
    end tell
end list_mailboxes


-- Return the plain-text body of the message with the given RFC 2822 Message-ID.
on get_body(messageId)
    set targetMsg to find_message(messageId)
    tell application "Mail"
        return content of targetMsg
    end tell
end get_body


-- Move the message with the given Message-ID to the named mailbox.
on move_message(messageId, mailboxName)
    set targetMsg to find_message(messageId)
    set targetMailbox to resolve_mailbox(mailboxName)
    tell application "Mail"
        move targetMsg to targetMailbox
    end tell
end move_message


-- Move the message with the given Message-ID to the Trash.
on delete_message(messageId)
    set targetMsg to find_message(messageId)
    tell application "Mail"
        delete targetMsg
    end tell
end delete_message


-- Permanently delete the named mailbox and all its contents.
-- Note: Mail's AppleScript delete frequently fails for IMAP/iCloud mailboxes
-- with a generic "-10000" because the removal must round-trip to the server.
-- Surface a clear, actionable message instead of the cryptic code.
on delete_mailbox(mailboxName)
    set targetMailbox to resolve_mailbox(mailboxName)
    tell application "Mail"
        try
            delete targetMailbox
        on error errMsg number errNum
            error "Could not delete mailbox '" & mailboxName & "' (" & errNum & "). Mail often refuses to delete IMAP/iCloud mailboxes via AppleScript; delete it manually in Mail.app (right-click the mailbox -> Delete Mailbox)."
        end try
    end tell
end delete_mailbox
