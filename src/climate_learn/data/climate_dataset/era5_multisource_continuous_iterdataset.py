# Standard library
import math
import os
import random
from typing import Union

# Third party
import numpy as np
import torch
from torch.utils.data import IterableDataset


class NpyReader(IterableDataset):
    def __init__(
        self,
        file_list,
        variables,
        out_variables,
        start_idx=0,
        end_idx=1,
        shuffle: bool = False,
        multi_dataset_training=True,
    ) -> None:
        super().__init__()
        start_idx = int(start_idx * len(file_list))
        end_idx = int(end_idx * len(file_list))
        file_list = file_list[start_idx:end_idx]
        self.file_list = [f for f in file_list if "climatology" not in f]
        self.variables = variables
        self.out_variables = out_variables if out_variables is not None else variables
        self.multi_dataset_training = multi_dataset_training
        self.shuffle = shuffle

    def __iter__(self):
        if self.shuffle:
            random.shuffle(self.file_list)

        n_files = len(self.file_list)
        worker_info = torch.utils.data.get_worker_info()
        
        if worker_info is None:
            iter_start = 0
            iter_end = n_files
        else:
            if not torch.distributed.is_initialized():
                rank = 0
                world_size = 1
            else:
                rank = torch.distributed.get_rank()
                world_size = torch.distributed.get_world_size()
            num_workers_per_ddp = worker_info.num_workers
            if self.multi_dataset_training:
                num_nodes = int(os.environ.get("NODES", None))
                num_gpus_per_node = int(world_size / num_nodes)
                num_shards = num_workers_per_ddp * num_gpus_per_node
                rank = rank % num_gpus_per_node
            else:
                num_shards = num_workers_per_ddp * world_size
            # per_worker = n_files // num_shards
            per_worker = int(math.floor(n_files / float(num_shards)))
            worker_id = rank * num_workers_per_ddp + worker_info.id
            iter_start = worker_id * per_worker
            iter_end = iter_start + per_worker

        for idx in range(iter_start, iter_end):
            path = self.file_list[idx]
            data = np.load(path)
            
            yield {k: np.squeeze(data[k], axis=1) for k in self.variables}, {
                k: np.squeeze(data[k], axis=1) for k in self.out_variables
            }, self.variables, self.out_variables


class Forecast(IterableDataset):
    def __init__(
        self, 
        dataset: NpyReader,
        random_lead_time: bool = True,
        min_pred_range=6,
        max_pred_range: int = 120,
        hrs_each_step: int = 1,
        history: int = 3,
        window: int = 6
    ) -> None:
        super().__init__()
        if not random_lead_time:
            assert min_pred_range == max_pred_range
        self.dataset = dataset
        self.random_lead_time = random_lead_time
        self.min_pred_range = min_pred_range
        self.max_pred_range = max_pred_range
        self.hrs_each_step = hrs_each_step
        self.history = history
        self.window = window

    def __iter__(self):
        for inp_data, out_data, variables, out_variables in self.dataset:
            inp_data = {
                k: torch.from_numpy(inp_data[k].astype(np.float32))
                .unsqueeze(0)
                .repeat_interleave(self.history, dim=0)
                for k in inp_data.keys()
            }
            out_data = {
                k: torch.from_numpy(out_data[k].astype(np.float32))
                for k in out_data.keys()
            }
            for key in inp_data.keys():
                for t in range(self.history):
                    inp_data[key][t] = inp_data[key][t].roll(-t * self.window, dims=0)

            last_idx = -((self.history - 1) * self.window + self.max_pred_range)

            inp_data = {
                k: inp_data[k][:, :last_idx].transpose(0, 1)
                for k in inp_data.keys()  # N, T, H, W
            }
            
            inp_data_len = inp_data[variables[0]].size(0)
            dtype = inp_data[variables[0]].dtype

            if self.random_lead_time:
                predict_ranges = torch.randint(
                    low=self.min_pred_range,
                    high=self.max_pred_range+1,
                    size=(inp_data_len,)
                )
            else:
                predict_ranges = torch.ones(inp_data_len).to(torch.long) * self.max_pred_range
            lead_times = self.hrs_each_step * predict_ranges / 100
            lead_times = lead_times.to(dtype)
            output_ids = torch.arange(inp_data_len) + (self.history - 1) * self.window + predict_ranges

            out_data = {k: out_data[k][output_ids] for k in out_data.keys()}
            yield inp_data, out_data, lead_times, variables, out_variables


class IndividualDataIter(IterableDataset):
    def __init__(
        self,
        dataset: Forecast,
        transforms: torch.nn.Module,
        output_transforms: torch.nn.Module,
        subsample: int = 6,
    ):
        super().__init__()
        self.dataset = dataset
        self.transforms = transforms
        self.output_transforms = output_transforms
        self.subsample = subsample

    def __iter__(self):
        for inp, out, lead_times, variables, out_variables in self.dataset:
            inp_shapes = set([inp[k].shape[0] for k in inp.keys()])
            out_shapes = set([out[k].shape[0] for k in out.keys()])
            assert len(inp_shapes) == 1
            assert len(out_shapes) == 1
            inp_len = next(iter(inp_shapes))
            out_len = next(iter(out_shapes))
            assert inp_len == out_len
            for i in range(0, inp_len, self.subsample):
                x = {k: inp[k][i] for k in inp.keys()}
                y = {k: out[k][i] for k in out.keys()}
                if self.transforms is not None:
                    x = {
                        k: self.transforms[k](x[k].unsqueeze(1)).squeeze(1)
                        for k in x.keys()
                    }
                if self.output_transforms is not None:
                    y = {
                        k: self.output_transforms[k](y[k].unsqueeze(0)).squeeze(0)
                        for k in y.keys()
                    }
                yield x, y, lead_times[i], variables, out_variables


class ShuffleIterableDataset(IterableDataset):
    def __init__(self, dataset: IndividualDataIter, buffer_size: int) -> None:
        super().__init__()
        assert buffer_size > 0
        self.dataset = dataset
        self.buffer_size = buffer_size

    def __iter__(self):
        buf = []
        for x in self.dataset:
            if len(buf) == self.buffer_size:
                idx = random.randint(0, self.buffer_size - 1)
                yield buf[idx]
                buf[idx] = x
            else:
                buf.append(x)
        random.shuffle(buf)
        while buf:
            yield buf.pop()