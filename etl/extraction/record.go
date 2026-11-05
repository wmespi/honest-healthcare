package extraction

// The parse record: data/anthem/parses/<file_id>.parquet, one row saying what
// the last parse of a file made of it. Parse reads it to decide whether to
// download the file again (etl/parse.md#when-parse-downloads-a-file).

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	parquet "github.com/parquet-go/parquet-go"
	"github.com/wmespi/honest-healthcare/etl/core"
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
	FinishedAt      time.Time  `parquet:"finished_at,timestamp(millisecond)"`
	// CompressedBytes is the download's Content-Length, 0 when unknown.
	CompressedBytes int64 `parquet:"compressed_bytes"`
	// The rows written, and the rows the Georgia NPI filter dropped.
	PriceRows           int64 `parquet:"price_rows"`
	ProviderRows        int64 `parquet:"provider_rows"`
	PriceRowsDropped    int64 `parquet:"price_rows_dropped"`
	ProviderRowsDropped int64 `parquet:"provider_rows_dropped"`
}

func recordPath(fileID int64) string {
	return filepath.Join(core.ParsesDir, fmt.Sprintf("%d.parquet", fileID))
}

// readRecord is a file's parse record, or nil if it has never been parsed.
func readRecord(fileID int64) (*parseRecord, error) {
	rows, err := parquet.ReadFile[parseRecord](recordPath(fileID))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read the parse record of file %d: %w", fileID, err)
	}
	if len(rows) != 1 {
		return nil, fmt.Errorf("%s holds %d rows, not 1", recordPath(fileID), len(rows))
	}
	return &rows[0], nil
}

func writeRecord(r parseRecord) error {
	return core.ReplaceParquet(recordPath(r.FileID), []parseRecord{r})
}

// scratchDir holds a file's Parquet while it is written. Parse moves it into
// place after a clean stream and deletes it after a failure, so one left
// behind means a parse stopped part way.
func scratchDir(fileID int64) string {
	return filepath.Join(filepath.Dir(core.PricesOutputDir), inflightSubdir, fmt.Sprintf("%d", fileID))
}

// stopIfStopped errors when an earlier parse of f stopped part way, so parse
// leaves the file alone until someone has looked at why (Critical Rule 2).
func stopIfStopped(f core.IndexFile) error {
	if _, err := os.Stat(scratchDir(f.FileID)); err == nil {
		return fmt.Errorf("a parse of %s (file %d) stopped part way and left %s: look at why, then delete the folder and run parse again",
			f.Name, f.FileID, scratchDir(f.FileID))
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}
