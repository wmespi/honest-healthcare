package hospitalrates

import (
	"archive/zip"
	"bufio"
	"bytes"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"path"
	"strings"
)

type format int

const (
	formatCSV format = iota
	formatJSON
)

func (f format) String() string {
	if f == formatJSON {
		return "JSON"
	}
	return "CSV"
}

// priceFile is a hospital's price file opened for streaming, past any zip,
// gzip and byte-order mark, at the first byte of its CSV or JSON text.
type priceFile struct {
	io.Reader
	format format
	// how says how it was unwrapped, for the log ("zip entry x.csv, gzip").
	how     []string
	closers []io.Closer
}

func (p *priceFile) Close() error {
	var err error
	for i := len(p.closers) - 1; i >= 0; i-- {
		if cerr := p.closers[i].Close(); err == nil {
			err = cerr
		}
	}
	return err
}

// openPriceFile sniffs the file's first bytes rather than trusting its name:
// some hospitals serve their file at an address with no extension. A zip is
// opened at its first .csv or .json entry, else its largest; a gzip stream is
// inflated; a UTF-8 byte-order mark is dropped. Then text starting with "{" is
// JSON, and anything else is CSV.
func openPriceFile(filePath string) (*priceFile, error) {
	f, err := os.Open(filePath)
	if err != nil {
		return nil, err
	}
	p := &priceFile{closers: []io.Closer{f}}
	br := bufio.NewReaderSize(f, 1<<20)
	head, _ := br.Peek(4)
	var r io.Reader = br
	if bytes.HasPrefix(head, []byte("PK\x03\x04")) {
		entry, name, err := openZipEntry(filePath)
		if err != nil {
			p.Close()
			return nil, err
		}
		p.closers = append(p.closers, entry)
		p.how = append(p.how, "zip entry "+name)
		br = bufio.NewReaderSize(entry, 1<<20)
		r = br
		head, _ = br.Peek(4)
	}
	if bytes.HasPrefix(head, []byte{0x1f, 0x8b}) {
		gz, err := gzip.NewReader(br)
		if err != nil {
			p.Close()
			return nil, fmt.Errorf("gzip: %w", err)
		}
		p.closers = append(p.closers, gz)
		p.how = append(p.how, "gzip")
		br = bufio.NewReaderSize(gz, 1<<20)
		r = br
	}
	if err := p.sniffText(br); err != nil {
		p.Close()
		return nil, err
	}
	p.Reader = r
	return p, nil
}

// sniffText drops a UTF-8 byte-order mark and decides JSON or CSV from the
// first byte that isn't white space.
func (p *priceFile) sniffText(br *bufio.Reader) error {
	head, _ := br.Peek(3)
	switch {
	case bytes.HasPrefix(head, []byte{0xef, 0xbb, 0xbf}):
		br.Discard(3)
		p.how = append(p.how, "BOM")
	case bytes.HasPrefix(head, []byte{0xff, 0xfe}), bytes.HasPrefix(head, []byte{0xfe, 0xff}):
		return fmt.Errorf("the file is UTF-16 text, which is not read")
	}
	text, _ := br.Peek(4096)
	trimmed := bytes.TrimLeft(text, " \t\r\n")
	if len(trimmed) == 0 {
		return fmt.Errorf("the file is empty")
	}
	if trimmed[0] == '{' {
		p.format = formatJSON
	}
	return nil
}

// openZipEntry opens the zip's first .csv or .json entry, or its largest when
// it has neither.
func openZipEntry(zipPath string) (io.ReadCloser, string, error) {
	zr, err := zip.OpenReader(zipPath)
	if err != nil {
		return nil, "", fmt.Errorf("open zip: %w", err)
	}
	var pick, largest *zip.File
	for _, e := range zr.File {
		base := path.Base(e.Name)
		if e.FileInfo().IsDir() || strings.HasPrefix(e.Name, "__MACOSX/") || strings.HasPrefix(base, "._") {
			continue
		}
		if largest == nil || e.UncompressedSize64 > largest.UncompressedSize64 {
			largest = e
		}
		if ext := strings.ToLower(path.Ext(base)); pick == nil && (ext == ".csv" || ext == ".json") {
			pick = e
		}
	}
	if pick == nil {
		pick = largest
	}
	if pick == nil {
		zr.Close()
		return nil, "", fmt.Errorf("the zip has no files")
	}
	rc, err := pick.Open()
	if err != nil {
		zr.Close()
		return nil, "", fmt.Errorf("open zip entry %s: %w", pick.Name, err)
	}
	return &zipEntry{ReadCloser: rc, zip: zr}, pick.Name, nil
}

// zipEntry closes the zip along with the entry.
type zipEntry struct {
	io.ReadCloser
	zip *zip.ReadCloser
}

func (z *zipEntry) Close() error {
	err := z.ReadCloser.Close()
	if zerr := z.zip.Close(); err == nil {
		err = zerr
	}
	return err
}
