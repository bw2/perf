#! /usr/bin/env python

# pylint: disable=C0103, C0301

from __future__ import print_function, division
import sys, argparse, gzip, json, ntpath
from os.path import splitext
from datetime import datetime
import multiprocessing as multi

from utils import rawcharCount, dotDict, getGC, get_targetids
from rep_utils import generate_repeats, get_ssrs, build_rep_set, fasta_ssrs
from fastq_utils import ssr_fastq_output, process_fastq, get_ssrs_fastq, fastq_ssrs
from analyse import analyse, analyse_fastq
from annotation import annotate

inf = float('inf')


def getArgs():
    """
    Parses command line arguments and returns them to the caller
    """
    __version__ = 'v0.4.0'
    parser = argparse.ArgumentParser()
    parser._action_groups.pop()

    required = parser.add_argument_group('Required arguments')
    required.add_argument('-i', '--input', required=True, metavar='<FILE>', help='Input sequence file.')
    
    optional = parser.add_argument_group('Optional arguments')
    optional.add_argument('--format', metavar='<STR>', default='fasta', help='Input file format. Default: fasta, Permissible: fasta, fastq')
    optional.add_argument('--version', action='version', version='PERF ' + __version__)
    
    #Basic options
    optional.add_argument('-o', '--output', type=argparse.FileType('r+'), metavar='<FILE>', default=sys.stdout, help='Output file name. Default: Input file name + _perf.tsv')
    optional.add_argument('--info', action='store_true', default=False, help='Sequence file info recorded in the output.')
    
    optional.add_argument('-rep', '--repeats', type=argparse.FileType('r'), metavar='<FILE>', help='File with list of repeats (Not allowed with -m and/or -M)')
    optional.add_argument('-a', '--analyse', action='store_true', default=False, help='Generate a summary HTML report.')
    optional.add_argument('-g', '--annotate', metavar='<FILE>', help='Genic annotation input file for annotation, Both GFF and GTF can be processed. Use --anno-format to specify format.')
    
    #Selections options based on motif size and seq lengths
    optional.add_argument('-m', '--min-motif-size', type=int, metavar='<INT>', default=1, help='Minimum size of a repeat motif in bp (Not allowed with -rep)')
    optional.add_argument('-M', '--max-motif-size', type=int, metavar='<INT>', default=6, help='Maximum size of a repeat motif in bp (Not allowed with -rep)')
    optional.add_argument('-s', '--min-seq-length', type=int, metavar = '<INT>', default=0, help='Minimum size of sequence length for consideration (in bp)')
    optional.add_argument('-S', '--max-seq-length', type=float, metavar='<FLOAT>', default=inf, help='Maximum size of sequence length for consideration (in bp)')
    optional.add_argument('--include-atomic', action='store_true', default=False, help='An option to include factor atomic repeats for minimum motif sizes greater than 1.')

    #Annotation options
    optional.add_argument('--anno-format', type=str, default='GFF', help='Format of genic annotation file. Valid inputs: GFF, GTF. Default: GFF')
    optional.add_argument("--gene-key", metavar="<STR>", default="gene", type=str, 
        help="""Attribute key for geneId. The default identifier for a GFF file is "gene"
         and for a GTF file is "gene_id". Please check the annotation file and pick a robust gene identifier from the attribute column.""")
    optional.add_argument('--up-promoter', metavar="<INT>", type=int, default=1000, help='Upstream distance(bp) from TSS to be considered as promoter region. Default 1000')
    optional.add_argument('--down-promoter', metavar="<INT>", type=int, default=1000, help='Downstream distance(bp) from TSS to be considered as promoter region. Default 1000')    
    
    #Cutoff options (min_length or min_units)    
    cutoff_group = optional.add_mutually_exclusive_group()
    cutoff_group.add_argument('-l', '--min-length', type=int, metavar='<INT>', help='Minimum length cutoff of repeat')
    cutoff_group.add_argument('-u', '--min-units', metavar='INT or FILE', help="Minimum number of repeating units to be considered. Can be an integer or a file specifying cutoffs for different motif sizes.")
    
    #Filter based on seqIds
    seqid_group = optional.add_mutually_exclusive_group()
    seqid_group.add_argument('-f', '--filter-seq-ids', metavar='<FILE>')
    seqid_group.add_argument('-F', '--target-seq-ids', metavar='<FILE>')

    #Multiprocessing threads
    optional.add_argument('-t', '--threads', type=int, metavar='<INT>', default=1, help='Number of threads to run the process on. Default is 1.')

    args = parser.parse_args()

    if args.repeats and (args.min_motif_size or args.max_motif_size):
        parser.error("-rep is not allowed with -m/-M")
    
    if args.output.name == "<stdout>":
        args.output = open(splitext(args.input)[0] + '_perf.tsv', 'w')

    return args


def ssr_native(args, length_cutoff=False, unit_cutoff=False):
    """
    Identifies microsatellites using native string matching.
    As the entire sequence is scanned in a single iteration, the speed is vastly improved
    """
    repeat_file = args.repeats
    if length_cutoff:
        length_cutoff = args.min_length
        repeats_info = build_rep_set(repeat_file, length_cutoff=length_cutoff)
        print('Using length cutoff of %d' % (length_cutoff), file=sys.stderr)
    elif unit_cutoff:
        repeats_info = build_rep_set(repeat_file, unit_cutoff=unit_cutoff)
        print('Using unit cutoff of ', unit_cutoff, file=sys.stderr)
    
    if args.format == 'fasta':
        fasta_ssrs(args, repeats_info)

    elif input_format == 'fastq':
        fastq_ssrs(args, repeats_info)


def main():
    """
    Main function of the script
    """
    args = getArgs()

    # User specifies motif size range instead of giving a repeats file
    if args.repeats is None:
        min_motif_size = args.min_motif_size
        max_motif_size = args.max_motif_size
        args.repeats = generate_repeats(min_motif_size, max_motif_size, args.include_atomic)

    # User specifies minimum length
    if args.min_length:
        ssr_native(args, length_cutoff=args.min_length)

    # User specific minimum number of units
    elif args.min_units:
        unit_cutoff = dict()
        try:
            args.min_units = int(args.min_units)
            unit_cutoff[0] = args.min_units
        except ValueError:
            try:
                with open(args.min_units, 'r') as input_units:
                    for line in input_units:
                        L = line.strip().split()
                        try:
                            L[0] = int(L[0])
                            L[1] = int(L[1])
                            if L[1] == 1:
                                print('Warning: Repeat unit of 1 used for size %d.' % (L[0]), file=sys.stderr)
                            unit_cutoff[L[0]] = L[1]
                        except ValueError:
                            sys.exit('Invalid file format given for minimum units. Refer to help for more details')
            except FileNotFoundError:
                sys.exit('Units file specified is not found. Please provide a valid file')
        ssr_native(args, unit_cutoff=unit_cutoff)

    # Default settings
    elif args.min_length is None and args.min_units is None:
        args.min_length = 12
        ssr_native(args, length_cutoff=args.min_length)
           
    if args.annotate is not None:
        annotate(args)

    # Specifies to generate a HTML report
    if args.analyse:
        analyse(args)

if __name__ == '__main__':
    main()
