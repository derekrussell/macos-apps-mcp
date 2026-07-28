-- utilities.applescript
-- Shared handlers for the apple-mcp AppleScript files (mail, reminders, notes).
--
-- These handlers were previously duplicated in every script. AppleScript has
-- no include mechanism for source files run via osascript, so each main script
-- loads this library at runtime with:
--
--   set util to run script (read POSIX file <this file> as «class utf8»)
--
-- and then calls handlers as:
--
--   util's sanitise_field(...)          -- outside a tell block
--   my (util's sanitise_field(...))     -- inside a tell application block
--
-- Returning the script object at the top level is what exposes the handlers
-- to the caller; running this file on its own simply returns the object.

script utilLib
    -- Replace each delimiter/newline character (|, LF, CR) with a space so a
    -- field can never break the pipe-delimited, one-record-per-line format.
    -- The split (text items) and join (as text) must use DIFFERENT delimiters:
    -- splitting and joining on the same delimiter is a no-op.
    on sanitise_field(str)
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

    -- Parse an ISO 8601 string (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS) into an
    -- AppleScript date by building it from components. AppleScript's own
    -- `date "..."` coercion is locale-dependent and mangles ISO strings
    -- (e.g. "2026-07-29T09:00:00" becomes 16 January 2035), so never use it.
    -- `day` is set to 1 before changing month/year to avoid end-of-month
    -- rollover (e.g. 31 January -> "31 February").
    on parse_iso_date(isoStr)
        set y to (text 1 thru 4 of isoStr) as integer
        set mo to (text 6 thru 7 of isoStr) as integer
        set d to (text 9 thru 10 of isoStr) as integer
        set hh to 0
        set mi to 0
        set ss to 0
        if (count of isoStr) ≥ 19 then
            set hh to (text 12 thru 13 of isoStr) as integer
            set mi to (text 15 thru 16 of isoStr) as integer
            set ss to (text 18 thru 19 of isoStr) as integer
        end if
        set theDate to current date
        set day of theDate to 1
        set year of theDate to y
        set month of theDate to mo
        set day of theDate to d
        set hours of theDate to hh
        set minutes of theDate to mi
        set seconds of theDate to ss
        return theDate
    end parse_iso_date

    -- Format a date as ISO 8601 (YYYY-MM-DDTHH:MM:SS) without shell invocation.
    on format_date(theDate)
        set y to year of theDate as text
        set mo to month of theDate as integer
        set d to day of theDate as integer
        set h to hours of theDate
        set mi to minutes of theDate
        set s to seconds of theDate
        if mo < 10 then set mo to "0" & mo
        if d < 10 then set d to "0" & d
        if h < 10 then set h to "0" & h
        if mi < 10 then set mi to "0" & mi
        if s < 10 then set s to "0" & s
        return y & "-" & mo & "-" & d & "T" & h & ":" & mi & ":" & s
    end format_date
end script

return utilLib
