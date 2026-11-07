package extraction

// The parse record: data/raw/anthem/parses/<stem>.parquet, one row saying what
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
	// AnthemUpdatedAt is the Last-Modified of the copy this parse read, so the
	// file is downloaded again once Anthem's differs. Empty for a fixture, or a
	// failure before the download started.
	AnthemUpdatedAt *time.Time `parquet:"anthem_updated_at,optional,timestamp(millisecond)"`
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

func recordPath(stem string) string {
	return filepath.Join(core.ParsesDir, stem+".parquet")
}

// readRecord is the parse record saved under f's stem, or nil if it has never
// been parsed.
func readRecord(f core.IndexFile) (*parseRecord, error) {
	rows, err := parquet.ReadFile[parseRecord](recordPath(f.Stem()))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read the parse record of %s: %w", f.Name, err)
	}
	if len(rows) != 1 {
		return nil, fmt.Errorf("%s holds %d rows, not 1", recordPath(f.Stem()), len(rows))
	}
	return &rows[0], nil
}

// ratesCopy is the Last-Modified of the copy whose Parquet a record leaves on
// disk. A parsed record's rates are the copy it read.
func (r *parseRecord) ratesCopy() *time.Time {
	if r == nil {
		return nil
	}
	if r.Status == statusParsed && r.RatesUpdatedAt == nil {
		return r.AnthemUpdatedAt
	}
	return r.RatesUpdatedAt
}

func writeRecord(r parseRecord) error {
	stem := core.IndexFile{Name: r.Name}.Stem()
	return core.ReplaceParquet(recordPath(stem), []parseRecord{r})
}

// scratchDir holds a file's Parquet while it is written. Parse moves it into
// place after a clean stream and deletes it after a failure, so one left
// behind means a parse stopped part way.
func scratchDir(f core.IndexFile) string {
	return filepath.Join(filepath.Dir(core.PricesOutputDir), inflightSubdir, f.Stem())
}

// stopIfStopped errors when an earlier parse of f stopped part way, so parse
// leaves the file alone until someone has looked at why (Critical Rule 2).
func stopIfStopped(f core.IndexFile) error {
	if _, err := os.Stat(scratchDir(f)); err == nil {
		return fmt.Errorf("a parse of %s stopped part way and left %s: look at why, then delete the folder and run parse again",
			f.Name, scratchDir(f))
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}
