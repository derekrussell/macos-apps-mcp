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
    set AppleScript's text item delimiters to "|"
    set str to (text items of str) as text
    set AppleScript's text item delimiters to linefeed
    set str to (text items of str) as text
    set AppleScript's text item delimiters to return
    set str to (text items of str) as text
    set AppleScript's text item delimiters to ""
    return str
end sanitise_field


-- Level 1: Check one mailbox by name; recurse into its children if not found.
on search_mailbox(mailboxName, mbx)
    tell application "Mail"
        if name of mbx is mailboxName then return mbx
        set subList to mailboxes of mbx
        if (count of subList) > 0 then
            return my find_mailbox_by_name(mailboxName, subList)
        end if
        return missing value
    end tell
end search_mailbox


-- Level 2: Iterate a list of mailboxes, delegating to search_mailbox.
on find_mailbox_by_name(mailboxName, mbxList)
    tell application "Mail"
        repeat with mbx in mbxList
            set found to my search_mailbox(mailboxName, mbx)
            if found is not missing value then return found
        end repeat
        return missing value
    end tell
end find_mailbox_by_name


-- Level 3: Search across all accounts. Returns inbox directly for "inbox".
on resolve_mailbox(mailboxName)
    tell application "Mail"
        if mailboxName is "inbox" then return inbox
        repeat with acct in every account
            set found to my find_mailbox_by_name(mailboxName, mailboxes of acct)
            if found is not missing value then return found
        end repeat
        error "Mailbox not found: " & mailboxName
    end tell
end resolve_mailbox


-- Level 1: Search one mailbox for a message by ID; recurse into its children.
on search_mailbox_for_message(messageId, mbx)
    tell application "Mail"
        try
            set targetMsg to first message of mbx whose message id is messageId
            return targetMsg
        end try
        set subList to mailboxes of mbx
        if (count of subList) > 0 then
            return my find_message_in_mailboxes(messageId, subList)
        end if
        return missing value
    end tell
end search_mailbox_for_message


-- Level 2: Iterate a list of mailboxes, delegating to search_mailbox_for_message.
on find_message_in_mailboxes(messageId, mbxList)
    tell application "Mail"
        repeat with mbx in mbxList
            set found to my search_mailbox_for_message(messageId, mbx)
            if found is not missing value then return found
        end repeat
        return missing value
    end tell
end find_message_in_mailboxes


-- Level 3: Search across all accounts.
on find_message(messageId)
    tell application "Mail"
        repeat with acct in every account
            set found to my find_message_in_mailboxes(messageId, mailboxes of acct)
            if found is not missing value then return found
        end repeat
        error "Message not found: " & messageId
    end tell
end find_message


-- Level 1: Format one mailbox as name|count and recurse into its children.
on collect_mailbox(mbx)
    tell application "Mail"
        set output to (name of mbx) & "|" & ((count of messages of mbx) as text) & linefeed
        set subList to mailboxes of mbx
        if (count of subList) > 0 then
            repeat with subMbx in subList
                set output to output & (my collect_mailbox(subMbx))
            end repeat
        end if
        return output
    end tell
end collect_mailbox


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
-- Output is pipe-delimited: name|count
on list_mailboxes()
    tell application "Mail"
        set output to ""
        repeat with acct in every account
            repeat with mbx in mailboxes of acct
                set output to output & (my collect_mailbox(mbx))
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
