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

script sharedHandlers
    -- Replace the delimiter and newline characters (|, LF, CR) with spaces so a
    -- field can never break the pipe-delimited, one-record-per-line wire format.
    on sanitise_field(inputValue)
        set fieldText to inputValue as text
        set fieldText to my replace_text(fieldText, "|", " ")
        set fieldText to my replace_text(fieldText, linefeed, " ")
        set fieldText to my replace_text(fieldText, return, " ")
        return fieldText
    end sanitise_field

    -- Replace every occurrence of searchText with replacementText.
    -- The split (text items) and the join (as text) MUST use DIFFERENT
    -- delimiters: splitting and joining on the same delimiter is a no-op, which
    -- is why the delimiter is set once for each step.
    on replace_text(sourceText, searchText, replacementText)
        set AppleScript's text item delimiters to searchText
        set splitPieces to text items of sourceText
        set AppleScript's text item delimiters to replacementText
        set rejoinedText to splitPieces as text
        set AppleScript's text item delimiters to ""
        return rejoinedText
    end replace_text

    -- Parse an ISO 8601 string (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS) into an
    -- AppleScript date by building it from components. AppleScript's own
    -- `date "..."` coercion is locale-dependent and mangles ISO strings
    -- (e.g. "2026-07-29T09:00:00" becomes 16 January 2035), so never use it.
    on parse_iso_date(isoString)
        set yearValue to (text 1 thru 4 of isoString) as integer
        set monthValue to (text 6 thru 7 of isoString) as integer
        set dayValue to (text 9 thru 10 of isoString) as integer

        -- The time part is optional; default to midnight when absent.
        set hourValue to 0
        set minuteValue to 0
        set secondValue to 0
        if (count of isoString) ≥ 19 then
            set hourValue to (text 12 thru 13 of isoString) as integer
            set minuteValue to (text 15 thru 16 of isoString) as integer
            set secondValue to (text 18 thru 19 of isoString) as integer
        end if

        -- Build the date from its components. Set the day to 1 BEFORE changing
        -- month/year to avoid end-of-month rollover (e.g. 31 January would
        -- otherwise spill into "31 February").
        set resultDate to current date
        set day of resultDate to 1
        set year of resultDate to yearValue
        set month of resultDate to monthValue
        set day of resultDate to dayValue
        set hours of resultDate to hourValue
        set minutes of resultDate to minuteValue
        set seconds of resultDate to secondValue
        return resultDate
    end parse_iso_date

    -- Format a date as ISO 8601 (YYYY-MM-DDTHH:MM:SS) without shell invocation.
    on format_date(theDate)
        set yearText to (year of theDate) as text
        set monthText to my pad_two_digits((month of theDate) as integer)
        set dayText to my pad_two_digits((day of theDate) as integer)
        set hourText to my pad_two_digits(hours of theDate)
        set minuteText to my pad_two_digits(minutes of theDate)
        set secondText to my pad_two_digits(seconds of theDate)
        return yearText & "-" & monthText & "-" & dayText & "T" & hourText & ":" & minuteText & ":" & secondText
    end format_date

    -- Return a number as text, zero-padded to at least two digits (e.g. 5 -> "05").
    on pad_two_digits(numberValue)
        if numberValue < 10 then
            return "0" & numberValue
        else
            return numberValue as text
        end if
    end pad_two_digits
end script

return sharedHandlers
