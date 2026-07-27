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
--   list_mailboxes                                 -> name|count\n...
--   get_body        <message_id>                   -> plain-text body
--   move            <message_id> <mailbox>         -> (no output)
--   delete          <message_id>                   -> (no output)
--   delete_mailbox  <mailbox>                      -> (no output)

on run argv
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

-- Strip pipe and newline characters from a string field.
-- Must be called with "my" from inside tell blocks so it runs in script scope,
-- ensuring text item delimiters resolve as an AppleScript language construct.
on sanitise_field(str)
    -- Replace each delimiter/newline character with a space. The split
    -- (text items) and join (as text) must use DIFFERENT delimiters:
    -- splitting and joining on the same delimiter is a no-op.
    set str to str as text
    set AppleScript's text item delimiters to "|"
    set theItems to text items of str
    set AppleScript's text item delimiters to " "
    set str to theItems as text
    set AppleScript's text item delimiters to linefeed
    set theItems to text items of str
    set AppleScript's text item delimiters to " "
    set str to theItems as text
    set AppleScript's text item delimiters to return
    set theItems to text items of str
    set AppleScript's text item delimiters to " "
    set str to theItems as text
    set AppleScript's text item delimiters to ""
    return str
end sanitise_field


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
                if (my mailbox_path(mbx)) is mailboxName then return mbx
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
            set msgDate to date sent of msg as text
            if read status of msg is true then
                set msgRead to "true"
            else
                set msgRead to "false"
            end if
            set output to output & msgId & "|" & (my sanitise_field(msgSubject)) & "|" & (my sanitise_field(msgSender)) & "|" & msgDate & "|" & msgRead & linefeed
        end repeat

        return output
    end tell
end get_messages


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
on delete_mailbox(mailboxName)
    set targetMailbox to resolve_mailbox(mailboxName)
    tell application "Mail"
        delete targetMailbox
    end tell
end delete_mailbox
