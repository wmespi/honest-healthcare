package core

import (
	"hash/fnv"
	"math"
	"path"
	"strings"
)

// IndexFile is one row of index_files.parquet: an in-network file this month's
// master index lists. `etl discover` writes the list, and parse finds the files
// targets.yaml names in it.
type IndexFile struct {
	FileID int64 `parquet:"file_id"`
	// Name is the last part of the address (`GA_JBNKMED0001.json.gz`), which
	// targets.yaml names files by. Names aren't unique: Blue plans mirror one
	// BlueCard file on many hosts.
	Name string `parquet:"name"`
	// Address is the location without the query a signed URL carries, so it stays
	// the same from month to month when the signature doesn't.
	Address string `parquet:"address"`
	// Location is the URL to download the file from this month.
	Location string `parquet:"location"`
}

// PlanLink is one row of index_file_plans.parquet: the index lists plan PlanID
// against file FileID.
type PlanLink struct {
	FileID int64  `parquet:"file_id"`
	PlanID string `parquet:"plan_id"`
}

// FileAddress is a file's location without the query a signed URL carries.
func FileAddress(location string) string {
	addr, _, _ := strings.Cut(location, "?")
	return addr
}

// FileName is the last part of a file's address.
func FileName(address string) string {
	return path.Base(address)
}

// Stem is the name a parsed file's Parquet and parse record are saved under:
// its file name without `.json.gz` (`GA_JBNKMED0001`). Parse only reads a file
// whose name matches one file in the index, so stems don't collide on disk.
func (f IndexFile) Stem() string {
	return strings.TrimSuffix(strings.TrimSuffix(f.Name, ".gz"), ".json")
}

// FileID is the ID a file's parsed rows carry and the plan link uses: a 63-bit
// FNV-1a hash of its address, so a file has the same ID every month without
// anything keeping count. Changing this breaks the link to every parsed file.
func FileID(address string) int64 {
	h := fnv.New64a()
	h.Write([]byte(address))
	return int64(h.Sum64() & math.MaxInt64)
}
