import os
import argparse
import json
import sys

from tqdm import tqdm

# note that DensePhrases is installed with editable mode
from densephrases import DensePhrases

# fixed setting
DUMP_DIR = 'DensePhrases/outputs/densephrases-multi_wiki-20181220/dump'
RUNFILE_DIR = "runs"
os.makedirs(RUNFILE_DIR, exist_ok=True)


class Retriever():
    def __init__(self, args):
        self.R_UNIT = args.r_unit
        self.TOP_K = args.top_k
        self.args = args
        self.initialize_retriever()

    def initialize_retriever(self):
        if self.args.r_unit == 'dynamic':
            assert self.args.query_encoder_phrase is not None
            assert self.args.query_encoder_sentence is not None
            self.load_dir = [self.args.query_encoder_phrase, self.args.query_encoder_sentence]
        else:
            self.load_dir = [self.args.query_encoder_name_or_dir]
        # load model
        self.model = DensePhrases(
            # change query encoder after re-training
            # p_load_dir=self.args.query_encoder_phrase,
            # s_load_dir=self.args.query_encoder_sentence,
            load_dir=self.load_dir,
            dump_dir=DUMP_DIR,
            index_name=self.args.index_name
        )

    def retrieve(self, single_query_or_queries_dict):
        queries_batch = []
        R_UNIT = self.args.retrieve_mode
        print(f'R_UNIT:{self.args.retrieve_mode}')
        
        if isinstance(single_query_or_queries_dict, dict):  # batch search
            queries, qids = single_query_or_queries_dict['queries'], single_query_or_queries_dict['qids']

            # batchify
            N = self.args.batch_size
            for i in range(0, len(queries), N):
                batch = queries[i:i+N]
                queries_batch.append(batch)

            with open(f"{RUNFILE_DIR}/{self.args.runfile_name}", "w") as fw:
                # generate runfile
                print(
                    f"generating runfile: {RUNFILE_DIR}/{self.args.runfile_name}")

                # iterate through batch
                idx = 0
                for batch_query in tqdm(queries_batch):
                    # retrieve
                    result, meta, meta = self.model.search(
                        batch_query, retrieval_unit=self.R_UNIT, top_k=self.TOP_K, return_meta=True, return_meta=True, agg_add_weight=self.args.agg_add_weight)

                    if self.args.static:
                        result_phrase, meta_phrase = self.model.search(
                            batch_query, retrieval_unit='phrase', top_k=self.TOP_K)

                        phrase_sentence = []
                        for sentences, phrase_answer_list in zip(result, result_phrase):
                            phrase_answer_list_no_subset = []

                            for answer in phrase_answer_list:
                                is_in = False
                                for pre_answer in phrase_answer_list_no_subset:
                                    if answer in pre_answer:
                                        is_in = True

                                if not is_in:
                                    phrase_answer_list_no_subset.append(answer)

                            phrase_sentence.append(
                                phrase_answer_list_no_subset + sentences)

                        result = phrase_sentence

                    # write to runfile
                    for i in range(len(result)):
                        fw.write(f"{qids[idx]}\t{result[i]}\t{meta[i]}\n")
                        idx += 1

            return None

        elif isinstance(single_query_or_queries_dict, str):  # online search
            result = self.model.search(
                single_query_or_queries_dict, retrieval_unit=self.R_UNIT, top_k=self.TOP_K)

            if self.args.static:
                phrase_sentence = []
                result_phrase, meta_phrase = self.model.search(
                    single_query_or_queries_dict, retrieval_unit='phrase', top_k=self.TOP_K)
                phrase_answer_list_no_subset = []
                for answer in result_phrase:
                    is_in = False
                    for pre_answer in phrase_answer_list_no_subset:
                        if answer in pre_answer:
                            is_in = True

                    if not is_in:
                        phrase_answer_list_no_subset.append(answer)

                phrase_sentence.append(phrase_answer_list_no_subset + result)

                result = phrase_sentence[0]

            return result
        else:
            raise NotImplementedError


if __name__ == "__main__":
    # parse arguments
    parser = argparse.ArgumentParser(
        description='Retrieve query-relevant collection with varying topK.')
    parser.add_argument('--query_encoder_name_or_dir', type=str, default="princeton-nlp/densephrases-multi",
                        help="query encoder name registered in huggingface model hub OR custom query encoder checkpoint directory")
    parser.add_argument('--index_name', type=str, default="start/1048576_flat_OPQ96_small",
                        help="index name appended to index directory prefix")
    parser.add_argument('--query_list_path', type=str, default="DensePhrases/densephrases-data/open-qa/nq-open/test_preprocessed.json",
                        help="use batch search by default")
    parser.add_argument('--single_query', type=str, default=None,
                        help="if presented do online search instead of batch search")
    parser.add_argument('--runfile_name', type=str, default="run.tsv",
                        help="output runfile name which indluces query id and retrieved collection")
    parser.add_argument('--batch_size', type=int, default=128,
                        help="#query to process with parallel processing")
    parser.add_argument('--retrieve_mode', type=str, default="sentence",
                        help="R UNIT")
    parser.add_argument('--agg_add_weight', type=bool, default=False,
                        help="weight scores for duplicate unit when aggregate")
    parser.add_argument("--truecase", action="store_true",
                        help="set True when we use case-sentive language model")
    parser.add_argument("--static", action="store_true")
    
    parser.add_argument('--r_unit', type=str, default='sentence')
    parser.add_argument('--top_k', type=int, default=100)
    
    parser.add_argument('--query_encoder_phrase', type=str, default=None,
                        help="custom query encoder checkpoint directory")
    parser.add_argument('--query_encoder_sentence', type=str, default=None,
                        help="custom query encoder checkpoint directory")

    args = parser.parse_args()

    # to prevent collision with DensePhrase native argparser
    sys.argv = [sys.argv[0]]

    # define input for retriever: batch or online search
    if args.single_query is None:
        with open(args.query_list_path, 'r') as fr:
            qa_data = json.load(fr)

            # get all query list
            queries, qids = [], []
            for sample in qa_data['data']:
                queries.append(sample['question'])
                qids.append(sample['id'])

        inputs = {
            'queries': queries,
            'qids': qids,
        }
    # single query
    else:
        inputs = args.single_query

    # initialize retriever
    retriever = Retriever(args)

    # run
    result = retriever.retrieve(single_query_or_queries_dict=inputs)
    if args.single_query is not None:
        print(f"query: {args.single_query}, result: {result}")
