import torch
import os
import numpy as np
from torch.utils.data import Dataset, ConcatDataset
import pickle

import global_var
from utils.diffusion_smoothing import DiffusionSmoothing
from models.torch_smpl4garment_zhou import TorchSMPL4GarmentZhou

level_smoothness = [0, 0.15]
level_smoothiter = [0, 80]
Ltype = "uniform"


def smooth_it(smoothing, smooth_level, smpl, thetas, betas, verts, garment_class):

    if smooth_level == -1:
        verts = torch.zeros_like(verts)
    elif smooth_level != 0:
        v_poseshaped = smpl.forward_poseshaped(
            theta=thetas.unsqueeze(0), beta=betas.unsqueeze(0),
            garment_class=garment_class)[0]
        unposed_gar_smooth = (v_poseshaped + verts).numpy()
        unposed_gar_smooth = smoothing.smooth(
            unposed_gar_smooth, smoothness=level_smoothness[smooth_level],
            Ltype=Ltype, n=level_smoothiter[smooth_level])
        verts = torch.from_numpy(unposed_gar_smooth.astype(np.float32)) - v_poseshaped
    return verts


class OneStyleShape(Dataset):

    def __init__(self, garment_class, shape_idx, style_idx, split, gender='female', smooth_level=0):
        super(OneStyleShape, self).__init__()

        self.garment_class = garment_class
        self.split, self.gender = split, gender
        self.style_idx, self.shape_idx = style_idx, shape_idx
        self.smooth_level = smooth_level

        data_dir = os.path.join(global_var.DATA_DIR, '{}_{}'.format(garment_class, gender))

        beta = np.load(os.path.join(data_dir, 'shape/beta_{}.npy'.format(shape_idx)))
        gamma = np.load(os.path.join(data_dir, 'style/gamma_{}.npy'.format(shape_idx)))

        thetas = []
        pose_order = []
        verts_d = []
        smooth_verts_d = []
        seq_idx = 0
        while True:
            seq_path = os.path.join(data_dir, 'pose/{}_{}/poses_{:03d}.npz'.format(shape_idx, style_idx, seq_idx))
            if not os.path.exists(seq_path):
                break
            data = np.load(seq_path)
            verts_d_path = os.path.join(data_dir, 'pose/{}_{}/unposed_{:03d}.npy'.format(shape_idx, style_idx, seq_idx))
            if not os.path.exists(verts_d_path):
                print("{} doesn't exist.".format(verts_d_path))
                seq_idx += 1
                continue

            thetas.append(data['thetas'])
            pose_order.append(data['pose_order'])
            verts_d.append(np.load(verts_d_path))

            if smooth_level == 1 and global_var.CACHED_SMOOTH:
                smooth_verts_d_path = os.path.join(
                    global_var.SMOOTH_OUT_DIR, '{}_{}'.format(garment_class, gender),
                    'pose/{}_{}/smooth_unposed_{:03d}.npy'.format(shape_idx, style_idx, seq_idx))
                if not os.path.exists(smooth_verts_d_path):
                    print("{} doesn't exist.".format(smooth_verts_d_path))
                    continue
                smooth_verts_d.append(np.load(smooth_verts_d_path))

            seq_idx += 1
            # print("Using just one sequence file")
            # break

        thetas = np.concatenate(thetas, axis=0)
        pose_order = np.concatenate(pose_order, axis=0)
        verts_d = np.concatenate(verts_d, axis=0)
        if smooth_level == 1 and global_var.CACHED_SMOOTH:
            smooth_verts_d = np.concatenate(smooth_verts_d, axis=0)

        if split is not None:
            assert(split in ['test', 'train'])
            test_orig_idx = np.load(global_var.SPLIT_FILE)['test']
            test_idx = np.in1d(pose_order, test_orig_idx)
            chosen_idx = np.where(test_idx)[0] if split == 'test' else np.where(~test_idx)[0]

            thetas = thetas[chosen_idx]
            verts_d = verts_d[chosen_idx]
            if smooth_level == 1 and global_var.CACHED_SMOOTH:
                smooth_verts_d = smooth_verts_d[chosen_idx]

        self.verts_d = torch.from_numpy(verts_d.astype(np.float32))
        self.thetas = torch.from_numpy(thetas.astype(np.float32))
        self.beta = torch.from_numpy(beta[:10].astype(np.float32))
        self.gamma = torch.from_numpy(gamma.astype(np.float32))
        if smooth_level == 1 and global_var.CACHED_SMOOTH:
            self.smooth_verts_d = torch.from_numpy(smooth_verts_d.astype(np.float32))
            return

        if self.smooth_level != 0 and self.smooth_level != -1:
            with open(os.path.join(global_var.DATA_DIR, global_var.GAR_INFO_FILE), 'rb') as f:
                class_info = pickle.load(f)
            num_v = len(class_info[garment_class]['vert_indices'])
            self.smoothing = DiffusionSmoothing(
                np.zeros((num_v, 3)), class_info[garment_class]['f'])
            self.smpl = TorchSMPL4GarmentZhou(gender=gender)
        else:
            self.smoothing = None
            self.smpl = None

    def __len__(self):
        return self.thetas.shape[0]

    def __getitem__(self, item):
        verts_d, theta, beta, gamma = self.verts_d[item], self.thetas[item], self.beta, self.gamma
        if self.smooth_level == 1 and global_var.CACHED_SMOOTH:
            verts_d = self.smooth_verts_d[item]
            return verts_d, theta, beta, gamma, item
        verts_d = smooth_it(self.smoothing, self.smooth_level, self.smpl,
                            theta, beta, verts_d, self.garment_class)
        return verts_d, theta, beta, gamma, item


class OneStyleShapeHF(OneStyleShape):
    def __init__(self, garment_class, shape_idx, style_idx, split, gender='female', smooth_level=0, smpl=None):
        super(OneStyleShapeHF, self).__init__(garment_class, shape_idx, style_idx, split, gender=gender,
                                              smooth_level=smooth_level)
        print("USING HF AS GROUNDTRUTH")

    def __getitem__(self, item):
        verts_d = self.verts_d[item]
        ret = super(OneStyleShapeHF, self).__getitem__(item)
        ret = (verts_d,) + ret
        return ret


class MultiStyleShape(Dataset):
    def __init__(self, garment_class, split=None, gender='female', smooth_level=0, smpl=None):
        super(MultiStyleShape, self).__init__()

        self.garment_class = garment_class
        self.smooth_level = smooth_level
        self.split, self.gender = split, gender
        self.smpl = smpl
        assert(gender in ['neutral', 'male', 'female'])
        assert(split in ['train', 'test', None, 'train_train',
                         'train_test', 'test_train', 'test_test'])

        self.one_style_shape_datasets = self.get_single_datasets()
        self.ds = ConcatDataset(self.one_style_shape_datasets)
        if smooth_level == 1 and global_var.CACHED_SMOOTH:
            print("Using Smoothing in the dataset")
            return
        if self.smooth_level != 0 and self.smooth_level != -1:
            print("Using Smoothing in the dataset")
            print(self.smooth_level, Ltype)
            with open(os.path.join(global_var.DATA_DIR, global_var.GAR_INFO_FILE), 'rb') as f:
                class_info = pickle.load(f)
            num_v = len(class_info[garment_class]['vert_indices'])
            self.smoothing = DiffusionSmoothing(
                np.zeros((num_v, 3)), class_info[garment_class]['f'])
            self.smpl = TorchSMPL4GarmentZhou(gender=gender)
        else:
            self.smoothing = None
            self.smpl = None

    def get_single_datasets(self):
        garment_class, split, gender = self.garment_class, self.split, self.gender
        data_dir = os.path.join(global_var.DATA_DIR, '{}_{}'.format(garment_class, gender))
        with open(os.path.join(data_dir, "pivots.txt"), "r") as f:
            train_pivots = [l.strip().split('_') for l in f.readlines()]
        test_pivots = []
        # print("USING FEW PIVOTS ONLY")
        # train_pivots = train_pivots[:3]
        # print(train_pivots)

        single_sl = 0
        if self.smooth_level == 1 and global_var.CACHED_SMOOTH:
            single_sl = 1

        # self.pivots = train_pivots + test_pivots
        one_style_shape_datasets = []
        eval_split = True if split in ['train_train', 'train_test',
                                       'test_train', 'test_test'] else False

        assert eval_split is False, "Splits not done yet"

        if not eval_split:
            for shi, sti in train_pivots:
                one_style_shape_datasets.append(
                    OneStyleShape(garment_class, shape_idx=shi, style_idx=sti, split=split, gender=gender,
                                  smooth_level=single_sl))
            for shi, sti in test_pivots:
                if split == 'train': continue
                one_style_shape_datasets.append(
                    OneStyleShape(garment_class, shape_idx=shi, style_idx=sti, split=split, gender=gender,
                                  smooth_level=single_sl))
        else:
            pose_split, shape_split = split.split('_')
            for shi, sti in train_pivots:
                if shape_split == 'test': continue
                one_style_shape_datasets.append(
                    OneStyleShape(garment_class, shape_idx=shi, style_idx=sti, split=pose_split, gender=gender,
                                  smooth_level=single_sl))
            for shi, sti in test_pivots:
                if shape_split == 'train': continue
                one_style_shape_datasets.append(
                    OneStyleShape(garment_class, shape_idx=shi, style_idx=sti, split=pose_split, gender=gender,
                                  smooth_level=single_sl))
        return one_style_shape_datasets

    def __len__(self):
        return len(self.ds)

    def __getitem__(self, item):
        verts, thetas, betas, gammas, _ = self.ds[item]
        if self.smooth_level == 1 and global_var.CACHED_SMOOTH:
            return verts, thetas, betas, gammas, item
        verts = smooth_it(self.smoothing, self.smooth_level,
                          self.smpl, thetas, betas, verts, self.garment_class)
        return verts, thetas, betas, gammas, item


def visualize():
    from models.smpl4garment import SMPL4Garment

    garment_class = 't-shirt'
    gender = 'female'
    split = None
    style_idx = '000'
    shape_idx = '008'
    smooth_level = 0

    smpl = SMPL4Garment(gender=gender)

    # gt_ds = MultiStyleShape(garment_class=garment_class, split=split, smooth_level=smooth_level, gender=gender)
    # gt_ds = OneStyleShape(garment_class=garment_class, shape_idx=shape_idx, style_idx=style_idx, split=split,
    #                       smooth_level=smooth_level, gender=gender)

    gt_ds = MultiStyleShape(garment_class=garment_class, split=None, smooth_level=smooth_level, gender=gender)
    print(len(gt_ds))
    gt_ds = MultiStyleShape(garment_class=garment_class, split='train', smooth_level=smooth_level, gender=gender)
    print(len(gt_ds))
    gt_ds = MultiStyleShape(garment_class=garment_class, split='test', smooth_level=smooth_level, gender=gender)
    print(len(gt_ds))

    for idx in np.random.randint(0, len(gt_ds), 4):

        verts, thetas, betas, gammas, item = gt_ds[idx]
        # verts, sverts, thetas, betas, gammas, item = gt_ds[idx]

        body_m, gar_m = smpl.run(theta=thetas.numpy(), beta=betas.numpy(), garment_class=garment_class,
                                 garment_d=verts.numpy())
        body_m.write_ply("/BS/cpatel/work/body_{}.ply".format(idx))
        gar_m.write_ply("/BS/cpatel/work/gar_{}.ply".format(idx))

        # _, gar_m = smpl.run(theta=thetas.numpy(), beta=betas.numpy(), garment_class=garment_class,
        #                     garment_d=sverts.numpy())
        # gar_m.write_ply("/BS/cpatel/work/gars_{}.ply".format(idx))


def save_smooth():
    garment_class = 't-shirt'
    gender = 'male'
    smooth_level = 1
    OUT_DIR = global_var.SMOOTH_OUT_DIR

    data_dir = os.path.join(global_var.DATA_DIR, '{}_{}'.format(garment_class, gender))
    with open(os.path.join(data_dir, "pivots.txt"), "r") as f:
        train_pivots = [l.strip().split('_') for l in f.readlines()]

    with open(os.path.join(global_var.DATA_DIR, global_var.GAR_INFO_FILE)) as f:
        class_info = pickle.load(f)
    num_v = len(class_info[garment_class]['vert_indices'])
    smoothing = DiffusionSmoothing(
        np.zeros((num_v, 3)), class_info[garment_class]['f'])
    smpl = TorchSMPL4GarmentZhou(gender=gender)

    for shape_idx, style_idx in train_pivots:
        beta = torch.from_numpy(np.load(os.path.join(
            data_dir, 'shape/beta_{}.npy'.format(shape_idx))).astype(np.float32)[:10])
        gamma = torch.from_numpy(np.load(os.path.join(
            data_dir, 'style/gamma_{}.npy'.format(shape_idx))).astype(np.float32))
        outdir = os.path.join(OUT_DIR, "{}_{}".format(garment_class, gender), "pose/{}_{}".format(shape_idx, style_idx))
        if not os.path.exists(outdir):
            os.makedirs(outdir)

        seq_idx = 0
        while True:
            seq_path = os.path.join(data_dir, 'pose/{}_{}/poses_{:03d}.npz'.format(shape_idx, style_idx, seq_idx))
            if not os.path.exists(seq_path):
                break
            data = np.load(seq_path)
            verts_d_path = os.path.join(data_dir,
                                        'pose/{}_{}/unposed_{:03d}.npy'.format(shape_idx, style_idx, seq_idx))
            if not os.path.exists(verts_d_path):
                print("{} doesn't exist.".format(verts_d_path))
                seq_idx += 1
                continue
            print(verts_d_path)
            thetas = torch.from_numpy(data['thetas'].astype(np.float32))
            verts_d = torch.from_numpy(np.load(verts_d_path).astype(np.float32))
            smooth_verts_d = []
            for theta, vert_d in zip(thetas, verts_d):
                svert_d = smooth_it(smoothing, smooth_level, smpl, theta, beta, vert_d, garment_class)
                smooth_verts_d.append(svert_d.numpy())
            smooth_verts_d = np.stack(smooth_verts_d)
            outpath = os.path.join(outdir, "smooth_unposed_{:03d}.npy".format(seq_idx))
            np.save(outpath, smooth_verts_d)

            seq_idx += 1


if __name__ == "__main__":
    visualize()
    # save_smooth()
    pass
