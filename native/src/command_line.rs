/// Quote one UTF-16 argument according to the Windows C runtime parsing rules.
/// No shell is involved; UTF-16 code units, including surrogate pairs, are preserved.
pub fn quote(value: &[u16]) -> Vec<u16> {
    let mut output = vec![b'"' as u16];
    let mut slashes = 0;
    for &ch in value {
        if ch == b'\\' as u16 {
            slashes += 1;
            continue;
        }
        output.extend(std::iter::repeat_n(
            b'\\' as u16,
            slashes * if ch == b'"' as u16 { 2 } else { 1 },
        ));
        if ch == b'"' as u16 {
            output.push(b'\\' as u16);
        }
        output.push(ch);
        slashes = 0;
    }
    output.extend(std::iter::repeat_n(b'\\' as u16, slashes * 2));
    output.push(b'"' as u16);
    output
}

#[cfg(test)]
mod tests {
    use super::quote;
    #[test]
    fn quotes_empty_spaces_backslashes_quotes_and_unicode() {
        for (input, expected) in [
            ("", "\"\""),
            ("鯛焼き space", "\"鯛焼き space\""),
            ("C:\\日本語\\", "\"C:\\日本語\\\\\""),
            ("a\"b", "\"a\\\"b\""),
        ] {
            assert_eq!(
                String::from_utf16(&quote(&input.encode_utf16().collect::<Vec<_>>())).unwrap(),
                expected
            );
        }
        assert_eq!(quote(&[0xd800]), vec![34, 0xd800, 34]);
    }
}
