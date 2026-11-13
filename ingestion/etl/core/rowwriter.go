package core

import (
	"os"
	"path/filepath"

	parquet "github.com/parquet-go/parquet-go"
)

// RowWriter streams rows to a ZSTD Parquet file beside its path and moves it
// into place once complete, so a reader never sees a half-written file and a
// failed run leaves the previous file where it was.
type RowWriter[T any] struct {
	path string
	f    *os.File
	pw   *parquet.GenericWriter[T]
	buf  []T
}

// NewRowWriter starts writing path + ".part", creating its folder.
func NewRowWriter[T any](path string) (*RowWriter[T], error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	f, err := os.Create(path + ".part")
	if err != nil {
		return nil, err
	}
	return &RowWriter[T]{path: path, f: f, buf: make([]T, 0, 4096),
		pw: parquet.NewGenericWriter[T](f, parquet.Compression(&parquet.Zstd))}, nil
}

// Add buffers a row and writes the buffer once it is full.
func (w *RowWriter[T]) Add(row T) error {
	w.buf = append(w.buf, row)
	if len(w.buf) < cap(w.buf) {
		return nil
	}
	return w.flush()
}

func (w *RowWriter[T]) flush() error {
	_, err := w.pw.Write(w.buf)
	w.buf = w.buf[:0]
	return err
}

// Finish writes what's buffered and moves the file into place, or removes it
// when the extraction (`err`) or the write failed, and returns that error.
func (w *RowWriter[T]) Finish(err error) error {
	if err == nil {
		err = w.flush()
	}
	if cerr := w.pw.Close(); err == nil {
		err = cerr
	}
	if cerr := w.f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(w.path + ".part")
		return err
	}
	return os.Rename(w.path+".part", w.path)
}
