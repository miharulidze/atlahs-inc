#!/usr/bin/env python3
"""
=============================================================================
ATLAHS NVTX Annotation Checker
=============================================================================

This tool checks if an nsys SQLite trace report contains all required NCCL 
NVTX annotations needed by the ATLAHS GOAL generator.

BACKGROUND
----------
NCCL can be compiled with special flags that emit NVTX markers for profiling.
ATLAHS requires these markers to extract communication patterns from traces.
There are three compilation flags that control different annotation types:

  1. ENABLE_API_NVTX     - Emits markers for collective API calls
                          Pattern: "ncclAllReduce():opCount 0x..."
                          
  2. ENABLE_ENQUEUE_NVTX - Emits markers for CollInfo (algorithm details) and
                          WorkElem (work decomposition)
                          Pattern: "collType 0 algo 1 proto 0 nChannels 32..."
                          Pattern: "nWarps 16 elemSize 4 chunkSize..."
                          
  3. ENABLE_INIT_NVTX    - Emits markers for communicator initialization,
                          including Ring and Tree topology information
                          Pattern: "Rings 00:0 1 2 3 ..."
                          Pattern: "Trees 00:..." 
                          Pattern: "commId 0x... nranks X"

For full ATLAHS functionality, ALL THREE flags should be enabled when building
NCCL. If ENABLE_INIT_NVTX is missing, the GOAL generator must synthesize
topology information, which may affect simulation accuracy.

USAGE
-----
    # Check a single file:
    python check_nvtx_annotations.py trace.sqlite

    # Check multiple files:
    python check_nvtx_annotations.py file1.sqlite file2.sqlite file3.sqlite

    # Check all SQLite files in a directory:
    python check_nvtx_annotations.py /path/to/traces/*.sqlite

    # Quick summary only (no per-file details):
    python check_nvtx_annotations.py -q *.sqlite

    # Show sample markers for debugging:
    python check_nvtx_annotations.py -s trace.sqlite

EXIT CODES
----------
    0 - All checked files have all annotations
    1 - Some files have missing annotations
    2 - Error (no files provided, file not found, etc.)

SEE ALSO
--------
    README_NVTX_ANNOTATIONS.md - How to compile NCCL with ATLAHS annotations

=============================================================================
"""

import sqlite3
import sys
import os
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional


# =============================================================================
# SQL Queries for detecting each annotation type
# =============================================================================

# ENABLE_INIT_NVTX queries
# These markers are emitted during communicator initialization
QUERY_RING_TOPOLOGY = """
    SELECT COUNT(*) FROM NVTX_EVENTS WHERE text LIKE '%Rings %'
"""  # Matches "Rings 00:0 1 2 3 ..." showing ring channel layout

QUERY_TREE_TOPOLOGY = """
    SELECT COUNT(*) FROM NVTX_EVENTS WHERE text LIKE '%Trees %'
"""  # Matches "Trees 00:..." showing tree channel layout

QUERY_COMM_INIT = """
    SELECT COUNT(*) FROM NVTX_EVENTS 
    WHERE text LIKE '%commId%' AND text LIKE '%nranks%'
"""  # Matches communicator init: "commId 0x... nranks 16 rank 0"

# ENABLE_API_NVTX query
# These markers wrap the public NCCL API calls
QUERY_COLLECTIVE_API = """
    SELECT COUNT(*) FROM NVTX_EVENTS WHERE text LIKE 'nccl%():%'
"""  # Matches "ncclAllReduce():opCount 0x..." etc.

# ENABLE_ENQUEUE_NVTX queries
# These markers provide algorithm and work decomposition details
QUERY_COLL_INFO = """
    SELECT COUNT(*) FROM NVTX_EVENTS 
    WHERE text LIKE 'collType %' AND text LIKE '%algo %'
"""  # Matches "collType 0 algo 1 proto 0 nChannels 32..."

QUERY_WORK_ELEM = """
    SELECT COUNT(*) FROM NVTX_EVENTS WHERE text LIKE 'nWarps %'
"""  # Matches "nWarps 16 elemSize 4 chunkSize..."


# =============================================================================
# Core Functions
# =============================================================================

def check_annotations(sqlite_path: str) -> Dict[str, int]:
    """
    Query an nsys SQLite file for ATLAHS NVTX annotation counts.
    
    Args:
        sqlite_path: Path to the nsys SQLite report file
        
    Returns:
        Dictionary with counts for each annotation type:
        - 'rings':     Ring topology markers (ENABLE_INIT_NVTX)
        - 'trees':     Tree topology markers (ENABLE_INIT_NVTX)  
        - 'comm_init': Communicator init markers (ENABLE_INIT_NVTX)
        - 'coll_api':  Collective API call markers (ENABLE_API_NVTX)
        - 'coll_info': CollInfo markers (ENABLE_ENQUEUE_NVTX)
        - 'work_elem': WorkElem markers (ENABLE_ENQUEUE_NVTX)
    """
    conn = sqlite3.connect(sqlite_path)
    cur = conn.cursor()
    
    results = {}
    
    # Query ENABLE_INIT_NVTX markers (topology and comm init)
    cur.execute(QUERY_RING_TOPOLOGY)
    results['rings'] = cur.fetchone()[0]
    
    cur.execute(QUERY_TREE_TOPOLOGY)
    results['trees'] = cur.fetchone()[0]
    
    cur.execute(QUERY_COMM_INIT)
    results['comm_init'] = cur.fetchone()[0]
    
    # Query ENABLE_API_NVTX markers (public API calls)
    cur.execute(QUERY_COLLECTIVE_API)
    results['coll_api'] = cur.fetchone()[0]
    
    # Query ENABLE_ENQUEUE_NVTX markers (algorithm details)
    cur.execute(QUERY_COLL_INFO)
    results['coll_info'] = cur.fetchone()[0]
    
    cur.execute(QUERY_WORK_ELEM)
    results['work_elem'] = cur.fetchone()[0]
    
    conn.close()
    return results


def determine_flags(results: Dict[str, int]) -> Tuple[bool, bool, bool]:
    """
    Determine which NCCL compilation flags were likely enabled.
    
    Args:
        results: Annotation counts from check_annotations()
        
    Returns:
        Tuple of (has_api_nvtx, has_enqueue_nvtx, has_init_nvtx)
    """
    # ENABLE_API_NVTX: Need collective API markers
    has_api = results['coll_api'] > 0
    
    # ENABLE_ENQUEUE_NVTX: Need either CollInfo or WorkElem
    has_enqueue = results['coll_info'] > 0 or results['work_elem'] > 0
    
    # ENABLE_INIT_NVTX: Need all three init markers (rings, trees, comm_init)
    has_init = (results['rings'] > 0 and 
                results['trees'] > 0 and 
                results['comm_init'] > 0)
    
    return has_api, has_enqueue, has_init


def print_report(path: str, results: Dict[str, int], verbose: bool = True) -> Tuple[bool, bool, bool]:
    """
    Print a formatted report of annotation status.
    
    Args:
        path: Path to the SQLite file
        results: Annotation counts from check_annotations()
        verbose: If True, print detailed breakdown; if False, just summary
        
    Returns:
        Tuple of (has_api, has_enqueue, has_init) booleans
    """
    basename = os.path.basename(path)
    has_api, has_enqueue, has_init = determine_flags(results)
    
    if verbose:
        print()
        print("=" * 70)
        print(f"File: {basename}")
        print("=" * 70)
        
        # Section 1: ENABLE_INIT_NVTX markers
        print()
        print("ENABLE_INIT_NVTX markers (topology & communicator info):")
        print(f"  Ring topology:    {results['rings']:6d}  {'✓' if results['rings'] > 0 else '✗ MISSING'}")
        print(f"  Tree topology:    {results['trees']:6d}  {'✓' if results['trees'] > 0 else '✗ MISSING'}")
        print(f"  Comm init:        {results['comm_init']:6d}  {'✓' if results['comm_init'] > 0 else '✗ MISSING'}")
        
        # Section 2: ENABLE_API_NVTX markers
        print()
        print("ENABLE_API_NVTX markers (collective API calls):")
        print(f"  Collective API:   {results['coll_api']:6d}  {'✓' if results['coll_api'] > 0 else '✗ MISSING'}")
        
        # Section 3: ENABLE_ENQUEUE_NVTX markers
        print()
        print("ENABLE_ENQUEUE_NVTX markers (algorithm & work details):")
        print(f"  CollInfo:         {results['coll_info']:6d}  {'✓' if results['coll_info'] > 0 else '✗ MISSING'}")
        print(f"  WorkElem:         {results['work_elem']:6d}  {'✓' if results['work_elem'] > 0 else '✗ MISSING'}")
        
        # Summary
        print()
        print("-" * 40)
        print("COMPILATION FLAGS STATUS:")
        print("-" * 40)
        print(f"  ENABLE_API_NVTX:     {'✓ ENABLED' if has_api else '✗ DISABLED/MISSING'}")
        print(f"  ENABLE_ENQUEUE_NVTX: {'✓ ENABLED' if has_enqueue else '✗ DISABLED/MISSING'}")
        print(f"  ENABLE_INIT_NVTX:    {'✓ ENABLED' if has_init else '✗ DISABLED/MISSING'}")
        print()
        
        all_present = has_api and has_enqueue and has_init
        if all_present:
            print("  ✓✓✓ ALL ANNOTATIONS PRESENT - Ready for ATLAHS ✓✓✓")
        else:
            print("  ✗✗✗ SOME ANNOTATIONS MISSING ✗✗✗")
            if not has_init:
                print("      → Missing ENABLE_INIT_NVTX (Ring/Tree topology, comm init)")
                print("        ATLAHS will synthesize topology (may affect accuracy)")
            if not has_api:
                print("      → Missing ENABLE_API_NVTX (Collective API calls)")
                print("        Cannot extract collective call information")
            if not has_enqueue:
                print("      → Missing ENABLE_ENQUEUE_NVTX (CollInfo, WorkElem)")
                print("        Cannot extract algorithm details")
    
    return has_api, has_enqueue, has_init


def print_summary(all_results: List[Tuple[str, Tuple[bool, bool, bool]]]) -> bool:
    """
    Print an overall summary for multiple files.
    
    Args:
        all_results: List of (path, (has_api, has_enqueue, has_init)) tuples
        
    Returns:
        True if all files have all annotations, False otherwise
    """
    print()
    print("=" * 70)
    print("OVERALL SUMMARY")
    print("=" * 70)
    
    all_ok = 0
    missing_init = 0
    missing_api = 0
    missing_enqueue = 0
    
    for path, (has_api, has_enqueue, has_init) in all_results:
        basename = os.path.basename(path)
        if has_api and has_enqueue and has_init:
            all_ok += 1
            print(f"  ✓ {basename}")
        else:
            missing = []
            if not has_init:
                missing.append("INIT")
                missing_init += 1
            if not has_api:
                missing.append("API")
                missing_api += 1
            if not has_enqueue:
                missing.append("ENQUEUE")
                missing_enqueue += 1
            print(f"  ✗ {basename} (missing: {', '.join(missing)})")
    
    print()
    print(f"Total: {all_ok}/{len(all_results)} files have all annotations")
    
    if missing_init > 0:
        print(f"  - {missing_init} files missing ENABLE_INIT_NVTX")
    if missing_api > 0:
        print(f"  - {missing_api} files missing ENABLE_API_NVTX")
    if missing_enqueue > 0:
        print(f"  - {missing_enqueue} files missing ENABLE_ENQUEUE_NVTX")
    
    return all_ok == len(all_results)


def get_sample_markers(sqlite_path: str, limit: int = 3) -> None:
    """
    Print sample NVTX markers from the trace for debugging.
    
    Args:
        sqlite_path: Path to the SQLite file
        limit: Number of samples per category
    """
    conn = sqlite3.connect(sqlite_path)
    cur = conn.cursor()
    
    print()
    print("-" * 40)
    print("SAMPLE MARKERS (for debugging):")
    print("-" * 40)
    
    # Sample Ring markers
    cur.execute(f"SELECT text FROM NVTX_EVENTS WHERE text LIKE '%Rings %' LIMIT {limit}")
    rows = cur.fetchall()
    if rows:
        print("\nRing topology samples:")
        for row in rows:
            text = row[0][:80] + "..." if len(row[0]) > 80 else row[0]
            print(f"  {text}")
    
    # Sample API markers
    cur.execute(f"SELECT text FROM NVTX_EVENTS WHERE text LIKE 'nccl%():%' LIMIT {limit}")
    rows = cur.fetchall()
    if rows:
        print("\nCollective API samples:")
        for row in rows:
            text = row[0][:80] + "..." if len(row[0]) > 80 else row[0]
            print(f"  {text}")
    
    # Sample CollInfo markers
    cur.execute(f"SELECT text FROM NVTX_EVENTS WHERE text LIKE 'collType %' LIMIT {limit}")
    rows = cur.fetchall()
    if rows:
        print("\nCollInfo samples:")
        for row in rows:
            text = row[0][:80] + "..." if len(row[0]) > 80 else row[0]
            print(f"  {text}")
    
    conn.close()


# =============================================================================
# CLI Interface
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Check nsys SQLite traces for ATLAHS NVTX annotations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s trace.sqlite                    # Check single file
  %(prog)s *.sqlite                        # Check multiple files  
  %(prog)s -q *.sqlite                     # Quick summary only
  %(prog)s -s trace.sqlite                 # Show sample markers

For more details, see README_NVTX_ANNOTATIONS.md
        """
    )
    
    parser.add_argument(
        'files', 
        nargs='+', 
        help='SQLite trace file(s) to check'
    )
    
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Quiet mode: only show summary, not per-file details'
    )
    
    parser.add_argument(
        '-s', '--samples',
        action='store_true',
        help='Show sample NVTX markers for debugging'
    )
    
    return parser.parse_args()


def main() -> int:
    """
    Main entry point.
    
    Returns:
        Exit code: 0 if all files OK, 1 if missing annotations, 2 if error
    """
    # Handle legacy usage (no argparse flags)
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nError: No SQLite file provided.")
        print("\nExample:")
        print("  python check_nvtx_annotations.py /path/to/trace.sqlite")
        return 2
    
    # Check if using old-style arguments (just files, no flags)
    # This maintains backward compatibility
    if not any(arg.startswith('-') for arg in sys.argv[1:]):
        files = sys.argv[1:]
        quiet = False
        samples = False
    else:
        args = parse_args()
        files = args.files
        quiet = args.quiet
        samples = args.samples
    
    all_results = []
    
    for path in files:
        # Skip non-existent files
        if not os.path.exists(path):
            print(f"\nError: File not found: {path}")
            continue
        
        # Skip non-SQLite files
        if not path.endswith('.sqlite'):
            print(f"\nSkipping non-SQLite file: {path}")
            continue
        
        try:
            # Check annotations
            results = check_annotations(path)
            status = print_report(path, results, verbose=not quiet)
            all_results.append((path, status))
            
            # Optionally show sample markers
            if samples:
                get_sample_markers(path)
                
        except sqlite3.OperationalError as e:
            print(f"\nError: Cannot read {path}: {e}")
            print("       Is this a valid nsys SQLite report?")
        except Exception as e:
            print(f"\nError processing {path}: {e}")
    
    # No valid files processed
    if not all_results:
        print("\nNo valid SQLite files were processed.")
        return 2
    
    # Print overall summary if multiple files
    if len(all_results) > 1:
        all_ok = print_summary(all_results)
    else:
        # Single file: check if all annotations present
        has_api, has_enqueue, has_init = all_results[0][1]
        all_ok = has_api and has_enqueue and has_init
    
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
