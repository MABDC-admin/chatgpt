local _M = {}

local PYTHON_PATH = "/usr/bin/python3"
local HELPER_PATH = "/www/wwwroot/quipper.mabdc.com/_guardian/auth_helper.py"

-- Escape a string for safe inclusion in JSON
local function json_escape(s)
    if type(s) ~= "string" then return tostring(s or "") end
    s = s:gsub('\\', '\\\\')
    s = s:gsub('"', '\\"')
    s = s:gsub('\n', '\\n')
    s = s:gsub('\r', '\\r')
    s = s:gsub('\t', '\\t')
    return s
end

-- Convert a Lua table to JSON string
function _M.table_to_json(t)
    if t == nil then return "null" end
    local ttype = type(t)
    if ttype == "string" then return '"' .. json_escape(t) .. '"' end
    if ttype == "number" then return tostring(t) end
    if ttype == "boolean" then return t and "true" or "false" end
    if ttype ~= "table" then return "null" end

    -- Check if array (sequential integer keys starting at 1)
    local is_array = true
    local max_n = 0
    for k, _ in pairs(t) do
        if type(k) ~= "number" or k < 1 or math.floor(k) ~= k then
            is_array = false
            break
        end
        if k > max_n then max_n = k end
    end
    if max_n == 0 and next(t) == nil then is_array = true end

    if is_array and max_n > 0 then
        local parts = {}
        for i = 1, max_n do
            parts[i] = _M.table_to_json(t[i])
        end
        return "[" .. table.concat(parts, ",") .. "]"
    else
        local parts = {}
        for k, v in pairs(t) do
            table.insert(parts, '"' .. json_escape(tostring(k)) .. '":' .. _M.table_to_json(v))
        end
        return "{" .. table.concat(parts, ",") .. "}"
    end
end

-- Simple JSON decoder (handles our known Python output format)
-- Parses: objects, arrays, strings, numbers, booleans, null
function _M.parse_json(str)
    if not str or str == "" then return nil, "empty input" end
    str = str:match("^%s*(.-)%s*$")  -- trim whitespace

    local pos = 1

    local function peek()
        return str:sub(pos, pos)
    end

    local function advance(n)
        n = n or 1
        pos = pos + n
    end

    local function skip_ws()
        while pos <= #str and str:sub(pos, pos):match("%s") do
            pos = pos + 1
        end
    end

    local parse_value  -- forward declaration

    local function parse_string()
        if peek() ~= '"' then return nil, "expected string" end
        advance() -- skip opening quote
        local result = {}
        while pos <= #str do
            local ch = str:sub(pos, pos)
            if ch == '\\' then
                advance()
                local esc = str:sub(pos, pos)
                if esc == '"' then table.insert(result, '"')
                elseif esc == '\\' then table.insert(result, '\\')
                elseif esc == 'n' then table.insert(result, '\n')
                elseif esc == 'r' then table.insert(result, '\r')
                elseif esc == 't' then table.insert(result, '\t')
                elseif esc == '/' then table.insert(result, '/')
                else table.insert(result, esc)
                end
                advance()
            elseif ch == '"' then
                advance() -- skip closing quote
                return table.concat(result)
            else
                table.insert(result, ch)
                advance()
            end
        end
        return nil, "unterminated string"
    end

    local function parse_number()
        local num_str = str:match("^-?%d+%.?%d*[eE]?[+-]?%d*", pos)
        if not num_str then return nil, "expected number" end
        advance(#num_str)
        return tonumber(num_str)
    end

    local function parse_object()
        if peek() ~= '{' then return nil, "expected object" end
        advance()
        local obj = {}
        skip_ws()
        if peek() == '}' then advance(); return obj end
        while true do
            skip_ws()
            local key, err = parse_string()
            if not key then return nil, err end
            skip_ws()
            if peek() ~= ':' then return nil, "expected colon" end
            advance()
            skip_ws()
            local val
            val, err = parse_value()
            if err then return nil, err end
            obj[key] = val
            skip_ws()
            if peek() == ',' then advance()
            elseif peek() == '}' then advance(); return obj
            else return nil, "expected comma or close brace" end
        end
    end

    local function parse_array()
        if peek() ~= '[' then return nil, "expected array" end
        advance()
        local arr = {}
        skip_ws()
        if peek() == ']' then advance(); return arr end
        while true do
            skip_ws()
            local val, err = parse_value()
            if err then return nil, err end
            table.insert(arr, val)
            skip_ws()
            if peek() == ',' then advance()
            elseif peek() == ']' then advance(); return arr
            else return nil, "expected comma or close bracket" end
        end
    end

    parse_value = function()
        skip_ws()
        local ch = peek()
        if ch == '"' then return parse_string()
        elseif ch == '{' then return parse_object()
        elseif ch == '[' then return parse_array()
        elseif ch == 't' then
            if str:sub(pos, pos+3) == "true" then advance(4); return true end
            return nil, "unexpected token"
        elseif ch == 'f' then
            if str:sub(pos, pos+4) == "false" then advance(5); return false end
            return nil, "unexpected token"
        elseif ch == 'n' then
            if str:sub(pos, pos+3) == "null" then advance(4); return nil end
            return nil, "unexpected token"
        elseif ch == '-' or (ch >= '0' and ch <= '9') then
            return parse_number()
        else
            return nil, "unexpected character: " .. ch
        end
    end

    return parse_value()
end

-- Call the Python auth helper
function _M.call_python(cmd, args)
    local input_json = _M.table_to_json({cmd = cmd, args = args or {}})
    -- Escape single quotes in JSON for shell safety
    local escaped = input_json:gsub("'", "'\\''")
    local handle = io.popen("echo '" .. escaped .. "' | " .. PYTHON_PATH .. " " .. HELPER_PATH, 'r')
    if not handle then
        return nil, "Failed to execute auth helper"
    end
    local result = handle:read('*a')
    local success, reason, code = handle:close()
    if not result or result == '' then
        return nil, "Empty response from auth helper"
    end
    local parsed, err = _M.parse_json(result)
    if err then
        return nil, "JSON parse error: " .. tostring(err) .. " raw: " .. result:sub(1, 200)
    end
    return parsed, nil
end

-- Send JSON response and exit
function _M.json_response(status, data)
    ngx.status = status
    ngx.header['Content-Type'] = 'application/json'
    ngx.header['Cache-Control'] = 'no-store'
    if type(data) == "table" then
        ngx.say(_M.table_to_json(data))
    elseif type(data) == "string" then
        ngx.say(data)
    else
        ngx.say('{}')
    end
    return ngx.exit(status)
end

-- Read request body
function _M.read_body()
    ngx.req.read_body()
    return ngx.req.get_body_data() or ''
end

-- Parse JSON request body
function _M.parse_body_json()
    local body = _M.read_body()
    if body == '' then return {} end
    local parsed, err = _M.parse_json(body)
    if err then return {} end
    return parsed or {}
end

-- Get a cookie value by name
function _M.get_cookie(name)
    local cookies = ngx.var.http_cookie
    if not cookies then return nil end
    -- Prepend semicolon so we can uniformly match "; name=value"
    -- Lua patterns don't support alternation, so this trick avoids partial name matches
    local search = "; " .. cookies
    local pattern = ";%s*" .. name .. "=([^;]*)"
    return search:match(pattern)
end

return _M
