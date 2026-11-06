package core

import (
	"fmt"
	"os"
	"path/filepath"

	parquet "github.com/parquet-go/parquet-go"
)

// WriteParquet writes rows to a new ZSTD Parquet file at path, creating its
// folder. The same rows in the same order always make the same bytes.
func WriteParquet[T any](path string, rows []T) error {
	if err := os.MkdirAll(filepath.Dir(path), os.ModePerm); err != nil {
		return err
	}
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := parquet.NewGenericWriter[T](f, parquet.Compression(&parquet.Zstd))
	if _, err := w.Write(rows); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	if err := w.Close(); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return f.Close()
}

// ReplaceParquet writes rows beside path and moves the file into place, so a
// reader sees the old file or the new one, never part of one.
func ReplaceParquet[T any](path string, rows []T) error {
	tmp := path + ".tmp"
	if err := WriteParquet(tmp, rows); err != nil {
		os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, path)
}
