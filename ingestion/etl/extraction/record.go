package extraction

// The parse record: data/raw/<payer>/parses/<stem>.parquet, one row saying what
// the last parse of a file made of it. Parse reads it to decide whether to
// download the file again (ingestion/etl/parse.md#when-parse-downloads-a-file).

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	parquet "github.com/parquet-go/parquet-go"
	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// What became of a file.
const (
	statusParsed = "parsed"
	// statusSkipped: the probe found nobody on a target plan before in_network,
	// so nothing was written. Nothing went wrong, so it isn't a failure.
	statusSkipped = "skipped"
	statusFailed  = "failed"
)

type parseRecord struct {
	FileID  int64  `parquet:"file_id"`
	Name    string `parquet:"name"`
	Address string `parquet:"address"`
	Status  string `parquet:"status"`
	// Reason says why the file failed or was skipped, and is empty when parsed.
	Reason string `parquet:"reason"`
	// UpdatedAt is the Last-Modified of the copy this parse read, so the file is
	// downloaded again once the payer's differs. Empty for a fixture, or a
	// failure before the download started.
	UpdatedAt *time.Time `parquet:"updated_at,optional,timestamp(millisecond)"`
	// RatesUpdatedAt is the Last-Modified of the copy whose Parquet is on disk,
	// the rates the publish step reads: this parse's copy when it parsed, and
	// otherwise the one before it left, since a failed or skipped parse writes
	// none. Empty when no parse has written the file's Parquet, or its copy
	// had no Last-Modified.
	RatesUpdatedAt *time.Time `parquet:"rates_updated_at,optional,timestamp(millisecond)"`
	FinishedAt     time.Time  `parquet:"finished_at,timestamp(millisecond)"`
	// CompressedBytes is the download's Content-Length, 0 when unknown.
	CompressedBytes int64 `parquet:"compressed_bytes"`
	// The rows written, and the rows the Georgia NPI filter dropped.
	PriceRows           int64 `parquet:"price_rows"`
	ProviderRows        int64 `parquet:"provider_rows"`
	PriceRowsDropped    int64 `parquet:"price_rows_dropped"`
	ProviderRowsDropped int64 `parquet:"provider_rows_dropped"`
}

// legacyUpdatedAt is the one column a parse record written before payers
// differ in: `anthem_updated_at` is what `updated_at` was called.
type legacyUpdatedAt struct {
	AnthemUpdatedAt *time.Time `parquet:"anthem_updated_at,optional,timestamp(millisecond)"`
}

func recordPath(p core.Payer, stem string) string {
	return filepath.Join(p.ParsesDir(), stem+".parquet")
}

// readRecord is the parse record saved under f's stem, or nil if it has never
// been parsed.
func readRecord(p core.Payer, f core.IndexFile) (*parseRecord, error) {
	return readRecordAt(recordPath(p, f.Stem()), f.Name)
}

func readRecordAt(path, name string) (*parseRecord, error) {
	rows, err := parquet.ReadFile[parseRecord](path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read the parse record of %s: %w", name, err)
	}
	if len(rows) != 1 {
		return nil, fmt.Errorf("%s holds %d rows, not 1", path, len(rows))
	}
	rec := rows[0]
	if rec.UpdatedAt == nil {
		if legacy, err := parquet.ReadFile[legacyUpdatedAt](path); err == nil && len(legacy) == 1 {
			rec.UpdatedAt = legacy[0].AnthemUpdatedAt
		}
	}
	return &rec, nil
}

// allRecords is every parse record the payer has saved.
func allRecords(p core.Payer) ([]parseRecord, error) {
	paths, err := filepath.Glob(filepath.Join(p.ParsesDir(), "*.parquet"))
	if err != nil {
		return nil, err
	}
	var recs []parseRecord
	for _, path := range paths {
		rec, err := readRecordAt(path, filepath.Base(path))
		if err != nil {
			return nil, err
		}
		if rec != nil {
			recs = append(recs, *rec)
		}
	}
	return recs, nil
}

// ratesCopy is the Last-Modified of the copy whose Parquet a record leaves on
// disk. A parsed record's rates are the copy it read.
func (r *parseRecord) ratesCopy() *time.Time {
	if r == nil {
		return nil
	}
	if r.Status == statusParsed && r.RatesUpdatedAt == nil {
		return r.UpdatedAt
	}
	return r.RatesUpdatedAt
}

func writeRecord(p core.Payer, r parseRecord) error {
	stem := core.IndexFile{Name: r.Name}.Stem()
	return core.ReplaceParquet(recordPath(p, stem), []parseRecord{r})
}

// scratchDir holds a file's Parquet while it is written. Parse moves it into
// place after a clean stream and deletes it after a failure, so one left
// behind means a parse stopped part way.
func scratchDir(p core.Payer, f core.IndexFile) string {
	return filepath.Join(p.RawDir(), inflightSubdir, f.Stem())
}

// stopIfStopped errors when an earlier parse of f stopped part way, so parse
// leaves the file alone until someone has looked at why (Critical Rule 2).
func stopIfStopped(p core.Payer, f core.IndexFile) error {
	if _, err := os.Stat(scratchDir(p, f)); err == nil {
		return fmt.Errorf("a parse of %s stopped part way and left %s: look at why, then delete the folder and run parse again",
			f.Name, scratchDir(p, f))
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}
