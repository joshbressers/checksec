#!/usr/bin/env python

"""Fast RPM analysis tool"""

from checksec import process_file, Elf
from elftools.common.exceptions import ELFError

import sys
import cStringIO

try:
    import libarchive
except ImportError:
    print >> sys.stderr, "Please install python-libarchive from PyPI"
    sys.exit(-1)

try:
    import rpm
except ImportError:
    print >> sys.stderr, "Please install rpm-python package"
    sys.exit(-1)

import os
import json
import stat
import multiprocessing
import threading

data = {}
opformat = "json"
lock = threading.Lock()


def analyze(rpmfile, show_errors=False):
    """Analyse single RPM file"""
    if not os.path.exists(rpmfile):
        # print >> sys.stderr, "%s doesn't exists!" % rpmfile
        return

    if not rpmfile.endswith(".rpm"):
        # print >> sys.stderr, "skipping %s " % rpmfile
        return

    try:
        a = libarchive.Archive(rpmfile)
    except Exception, exc:
        print >> sys.stderr, rpmfile, str(exc)
        return

    try:
        ts = rpm.TransactionSet()
        fd = os.open(rpmfile, os.O_RDONLY)
        h = ts.hdrFromFdno(fd)
        os.close(fd)
    except Exception, exc:
        print >> sys.stderr, rpmfile, str(exc)
        return

    package = h[rpm.RPMTAG_NAME]
    group = h[rpm.RPMTAG_GROUP]
    # for i in range(0, len(files)):
        # fname = files[i]
        # mode = modes[i]
        # print fname, mode
        # if mode & 0111:
        #    efiles.append(fname)

    output = {}
    output["package"] = package
    output["group"] = group
    output["build"] = os.path.basename(rpmfile)
    output["files"] = []
    output["daemon"] = False
    flag = False
    directory = False

    for entry in a:
        size = entry.size

        # check if package is a daemon
        if "/etc/rc.d/init.d" in entry.pathname or \
           "/lib/systemd" in entry.pathname:
            output["daemon"] = True

        # skip 0 byte files only
        if size == 0 and not stat.S_ISDIR(entry.mode):
            continue

        # we are only interested in particular kind of directories
        if stat.S_ISDIR(entry.mode):
            if not ((entry.mode & stat.S_ISUID) or
                    (stat.S_ISGID & entry.mode)):
                continue
            else:
                flag = True
                directory = True

        if not entry.mode & 0111:
            continue

        # always report setuid files
        if ((entry.mode & stat.S_ISUID) or (stat.S_ISGID & entry.mode)):
            flag = True

        # skip library files
        filename = entry.pathname.lstrip(".")
        if ("lib" in filename and ".so" in filename) or \
           filename.endswith(".so"):
            continue

        try:
            contents = a.read(size)
        except Exception:
            continue

        # invoke checksec
        returncode = -1
        try:
            fh = cStringIO.StringIO(contents)
            elf = Elf(fh)
            out = process_file(elf)
            dataline = "%s,%s,%s,%s" % (package, os.path.basename(rpmfile),
                                        filename, out)
            returncode = 0
        except ELFError as exc:
            if show_errors:
                print >> sys.stderr, "%s,%s,Not an ELF binary" % \
                    (filename, str(exc))
            continue
        except IOError as exc:
            if show_errors:
                print >> sys.stderr, "%s,%s,Not an ELF binary" % \
                    (filename, str(exc))
            continue

        if returncode == 0 or flag:
            # populate fileinfo object
            fileinfo = {}
            fileinfo["name"] = filename
            fileinfo["size"] = entry.size
            fileinfo["mode"] = entry.mode
            if directory:
                fileinfo["directory"] = directory
            output["files"].append(fileinfo)
        if returncode == 0 and opformat == "csv":
            print(dataline)
        else:
            # print >> sys.stderr, dataline
            pass
        if returncode == 0 and opformat == "json":
            try:
                for kvp in out.split(","):
                    key, value = kvp.split("=")
                    fileinfo[key] = value
            except Exception:
                pass
    a.close()

    if opformat == "json":
        return json.dumps(output)


def profile_main():
    # Run 'main' redirecting its output to readelfout.txt
    # Saves profiling information in readelf.profile
    PROFFILE = 'readelf.profile'
    import cProfile
    cProfile.run('main()', PROFFILE)

    # Dig in some profiling stats
    import pstats
    p = pstats.Stats(PROFFILE)
    p.sort_stats('cumulative').print_stats(200)


def output_callback(result):
    with lock:
        if result:
            print(result)

def main():
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: %s <path to RPM files> " \
            "[output format (csv / json)] [existing JSON file]\n" \
            % sys.argv[0])
        sys.exit(-1)

    path = sys.argv[1]

    global opformat
    if (len(sys.argv) > 2):
        opformat = sys.argv[2]
    else:
        opformat = "csv"

    parallel = True
    # parallel = False
    if parallel:
        p = multiprocessing.Pool(2) # FIXME add autodetection?

    # pruning code to make analysis faster
    if (len(sys.argv) > 3):
        with open(sys.argv[3]) as f:
            for line in f.readlines():
                line = line.rstrip()
                try:
                    build = json.loads(line)
                    data[build["build"]] = build
                except Exception as exc:
                    print(str(exc))
                    sys.exit(1)

    outputmap = {}
    if(os.path.isfile(path)):
        sys.stderr.write("Analyzing %s ...\n" % path)
        out = analyze(path)
        if out:
            print(out)
    else:
        for (path, _, files) in os.walk(path):
            for fname in files:
                rpmfile = os.path.join(path, fname)
                #if os.path.basename(rpmfile) in data:
                    # print >> sys.stderr, "Skipping", rpmfile
                #    pass
                if parallel:
                    outputmap[rpmfile] = p.apply_async(analyze, (rpmfile,),
                            callback = output_callback)
                else:
                    out = analyze(rpmfile)
                    if out:
                        print(out)

    if parallel:
        p.close()
        p.join()

if __name__ == "__main__":
    main()
    # profile_main()