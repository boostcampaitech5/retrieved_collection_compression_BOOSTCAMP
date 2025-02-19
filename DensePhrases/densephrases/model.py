import copy
import logging
import numpy as np
import os

from densephrases import Options
from densephrases.utils.single_utils import load_encoder
from densephrases.utils.open_utils import load_phrase_index, get_query2vec, load_qa_pairs
from densephrases.utils.squad_utils import TrueCaser

logger = logging.getLogger(__name__)


class DensePhrases(object):
    def __init__(self,
                #  p_load_dir,
                #  s_load_dir,
                load_dir,
                 dump_dir,
                 index_name='start/1048576_flat_OPQ96',
                 device='cuda',
                 verbose=False,
                 **kwargs):
        print(
            "This could take up to 15 mins depending on the file reading speed of HDD/SSD")

        # Turn off loggers
        if not verbose:
            logging.getLogger("densephrases").setLevel(logging.WARNING)
            logging.getLogger("transformers").setLevel(logging.WARNING)

        # Get default options
        options = Options()
        options.add_model_options()
        options.add_index_options()
        options.add_retrieval_options()
        options.add_data_options()
        self.args = options.parse()

        # Set options
        # load encoder
        if len(load_dir) > 1:
            self.args.p_load_dir = load_dir[0] # phrase query encoder
            self.args.s_load_dir = load_dir[1] # sentence query encoder
            self.set_p_encoder(load_dir[0], device)
            self.set_s_encoder(load_dir[1], device)
        else:
            self.args.load_dir = load_dir[0]
            self.set_encoder(load_dir, device)

        self.args.dump_dir = dump_dir
        self.args.cache_dir = os.environ['CACHE_DIR']
        self.args.index_name = index_name
        self.args.cuda = True if device == 'cuda' else False
        self.args.__dict__.update(kwargs)

        # Load MIPS
        self.mips = load_phrase_index(self.args, ignore_logging=not verbose)

        # Others
        self.truecase = TrueCaser(os.path.join(
            os.environ['DATA_DIR'], self.args.truecase_path))
        print("Loading DensePhrases Completed!")

    def search(self, query='', retrieval_unit='phrase', top_k=10, truecase=True, return_meta=False, agg_add_weight=False):
        # If query is str, single query
        single_query = False
        if type(query) == str:
            batch_query = [query]
            single_query = True
        else:
            assert type(query) == list
            batch_query = query

        # Pre-processing
        if truecase:
            batch_query = [self.truecase.get_true_case(query) if query == query.lower() else query for query in batch_query]

        # Get question vector
        if retrieval_unit == 'dynamic':
            p_outs = self.p_query2vec(batch_query)
            s_outs = self.s_query2vec(batch_query)
            p_start = np.concatenate([out[0] for out in p_outs], 0)
            s_start = np.concatenate([out[0] for out in s_outs], 0)
            p_end = np.concatenate([out[1] for out in p_outs], 0)
            s_end = np.concatenate([out[1] for out in s_outs], 0)
            p_query_vec = np.concatenate([p_start, p_end], 1)
            s_query_vec = np.concatenate([s_start, s_end], 1)
            query_vec = [p_query_vec, s_query_vec]
        else:
            outs = self.query2vec(batch_query)
            start = np.concatenate([out[0] for out in outs], 0)
            end = np.concatenate([out[1] for out in outs], 0)
            query_vec = [np.concatenate([start, end], 1)]

        # Search
        agg_strats = {'phrase': 'opt1', 'sentence': 'opt2', 'paragraph': 'opt2', 'document': 'opt3', 'dynamic':'opt0'}
        if retrieval_unit not in agg_strats:
            raise NotImplementedError(
                f'"{retrieval_unit}" not supported. Choose one of {agg_strats.keys()}.')
        search_top_k = top_k
        if retrieval_unit in ['sentence', 'paragraph', 'document']:
            search_top_k *= 2

        rets = self.mips.search(
            query_vec, q_texts=batch_query, nprobe=256,
            top_k=search_top_k, max_answer_length=10,
            return_idxs=False, aggregate=True, agg_strat=agg_strats[retrieval_unit],
            return_sent=True if retrieval_unit == 'sentence' or 'dynamic' else False,
            agg_add_weight=agg_add_weight
        )

        # Gather results
        rets = [ret[:top_k] for ret in rets]
        if retrieval_unit == 'phrase':
            retrieved = [[rr['answer'] for rr in ret][:top_k] for ret in rets]
        elif retrieval_unit == 'sentence':
            retrieved = [[rr['context'] for rr in ret][:top_k] for ret in rets]
        elif retrieval_unit == 'paragraph':
            retrieved = [[rr['context'] for rr in ret][:top_k] for ret in rets]
        elif retrieval_unit == 'document':
            retrieved = [[rr['title'][0] for rr in ret][:top_k] for ret in rets]
        elif retrieval_unit == 'dynamic': # phrase and sentence
            retrieved = [[rr['answer'] if rr['unit']=='phrase' else rr['context'] for rr in ret][:top_k] for ret in rets]
        else:
            raise NotImplementedError()

        if single_query:
            rets = rets[0]
            retrieved = retrieved[0]

        if return_meta:
            return retrieved, rets
        else:
            return retrieved
        
    def set_encoder(self, load_dir, device='cuda'):
        self.args.load_dir = load_dir
        self.model, self.tokenizer, self.config = load_encoder(
            device, self.args)
        self.query2vec = get_query2vec(
            query_encoder=self.model, tokenizer=self.tokenizer, args=self.args, batch_size=64
        )
    def set_p_encoder(self, load_dir, device='cuda'):
        self.args.load_dir = load_dir
        self.model, self.tokenizer, self.config = load_encoder(device, self.args)
        self.p_query2vec = get_query2vec(
            query_encoder=self.model, tokenizer=self.tokenizer, args=self.args, batch_size=64
        )
    def set_s_encoder(self, load_dir, device='cuda'):
        self.args.load_dir = load_dir
        self.model, self.tokenizer, self.config = load_encoder(device, self.args)
        self.s_query2vec = get_query2vec(
            query_encoder=self.model, tokenizer=self.tokenizer, args=self.args, batch_size=64
        )

    def evaluate(self, test_path, **kwargs):
        from eval_phrase_retrieval import evaluate as evaluate_fn

        # Set new arguments
        new_args = copy.deepcopy(self.args)
        new_args.test_path = test_path
        new_args.truecase = True
        new_args.__dict__.update(kwargs)

        # Run with new_arg
        evaluate_fn(new_args, self.mips, self.model, self.tokenizer)
