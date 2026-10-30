use crate::MAX_MESSAGE_BYTES;
use serde_json::Value;
use std::io::{self, Read, Write};

pub fn read(reader: &mut impl Read) -> io::Result<Value> {
    let mut prefix = [0; 4];
    reader.read_exact(&mut prefix)?;
    let size = u32::from_le_bytes(prefix) as usize;
    if size == 0 || size > MAX_MESSAGE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid frame size",
        ));
    }
    let mut data = vec![0; size];
    reader.read_exact(&mut data)?;
    serde_json::from_slice(&data).map_err(io::Error::other)
}

pub fn write(writer: &mut impl Write, value: &Value) -> io::Result<()> {
    write_text(writer, &serde_json::to_string(value)?)
}

pub fn write_text(writer: &mut impl Write, text: &str) -> io::Result<()> {
    let bytes = text.as_bytes();
    if bytes.is_empty() || bytes.len() > MAX_MESSAGE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "frame exceeds 1 MiB",
        ));
    }
    writer.write_all(&(bytes.len() as u32).to_le_bytes())?;
    writer.write_all(bytes)?;
    writer.flush()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::Cursor;

    #[test]
    fn unicode_and_fragmented_reads() {
        let value = json!({"message": "鯛焼き🐟"});
        let mut bytes = Vec::new();
        write(&mut bytes, &value).unwrap();
        struct Fragmented(Cursor<Vec<u8>>);
        impl Read for Fragmented {
            fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
                let size = buffer.len().min(1);
                self.0.read(&mut buffer[..size])
            }
        }
        assert_eq!(read(&mut Fragmented(Cursor::new(bytes))).unwrap(), value);
    }

    #[test]
    fn rejects_bad_lengths_and_truncation() {
        for size in [0, MAX_MESSAGE_BYTES as u32 + 1, u32::MAX] {
            assert!(read(&mut Cursor::new(size.to_le_bytes())).is_err());
        }
        assert!(read(&mut Cursor::new([3, 0, 0, 0, b'{'])).is_err());
        assert!(read(&mut Cursor::new([1, 0, 0, 0, 0xff])).is_err());
    }
}
