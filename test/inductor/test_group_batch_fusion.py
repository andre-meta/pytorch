# Owner(s): ["module: inductor"]

import collections
import unittest

import torch
import torch._inductor
from torch._dynamo.test_case import run_tests, TestCase
from torch._dynamo.utils import counters
from torch.testing._internal.inductor_utils import HAS_CUDA

try:
    # importing this will register fbgemm lowerings for inductor
    import deeplearning.fbgemm.fbgemm_gpu.fb.inductor_lowerings  # noqa: F401

    has_fbgemm = True
except Exception:
    has_fbgemm = False
    pass

requires_cuda = unittest.skipUnless(HAS_CUDA, "requires cuda")


class MyModule(torch.nn.Module):
    def __init__(self, z: int, has_bias: bool, device="cuda") -> None:
        super().__init__()
        self.z = z
        self.device = device
        self.seq_len = 10
        self.seq1 = [
            torch.nn.Linear(z, z, has_bias).to(self.device) for _ in range(self.seq_len)
        ]
        self.seq2 = [
            torch.nn.Linear(z, z, has_bias).to(self.device) for _ in range(self.seq_len)
        ]
        self.seq3 = [
            torch.nn.Linear(z, z, has_bias).to(self.device) for _ in range(self.seq_len)
        ]

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x1 = [x + 0.1 * i for i in range(self.seq_len)]
        x2 = [self.seq1[i](x1[i]) for i in range(self.seq_len)]
        x3 = [x2[i] - 0.1 * i for i in range(self.seq_len)]
        x4 = [x1[i] for i in range(3)] + [x3[i] for i in range(3, self.seq_len)]
        x5 = [self.seq2[i](x4[i]) for i in range(self.seq_len)]
        x6 = [x5[i] + 0.1 * (self.seq_len - i) for i in range(self.seq_len)]
        x7 = (
            [x1[i] for i in range(4)]
            + [x3[i] for i in range(6, 8)]
            + [x6[i] for i in range(4)]
        )
        x8 = [self.seq3[i](x7[i]) for i in range(self.seq_len)]
        x9 = torch.cat(x8, dim=1)
        return x9


class MyModule2(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.linear0 = torch.nn.Linear(6, 8)
        self.linear1 = torch.nn.Linear(8, 8)
        self.linear2 = torch.nn.Linear(10, 8)
        self.linear3 = torch.nn.Linear(6, 8)
        self.linear4 = torch.nn.Linear(8, 8)
        self.linear5 = torch.nn.Linear(10, 8)
        self.bn0 = torch.nn.BatchNorm1d(8)
        self.bn1 = torch.nn.BatchNorm1d(8)
        self.bn2 = torch.nn.BatchNorm1d(8)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        t = torch.split(x, [6, 8, 10], dim=1)
        a0 = self.bn0(self.linear0(t[0] + 0.1))
        a1 = self.bn1(self.linear1(t[1] + 0.2))
        a2 = self.bn2(self.linear2(t[2] + 0.3))
        a3 = self.linear3(torch.sin(t[0]))
        a4 = self.linear4(torch.cos(t[1]))
        a5 = self.linear5(torch.sin(t[2] * 0.5))

        b = torch.cat([a0, a1, a2, a3, a4, a5])
        return torch.sigmoid(b)


class MyModule3(torch.nn.Module):
    def __init__(self, device, has_weight=True, has_bias=True):
        super().__init__()
        self.device = device
        self.scale0 = torch.nn.ParameterList(
            [torch.nn.Parameter(torch.randn(10)) for _ in range(5)]
        ).to(self.device)
        self.bias0 = torch.nn.ParameterList(
            [torch.nn.Parameter(torch.randn(10)) for _ in range(5)]
        ).to(self.device)
        self.scale1 = (
            torch.nn.ParameterList(
                [torch.nn.Parameter(torch.randn(5, 10)) for _ in range(5)]
            ).to(self.device)
            if has_weight
            else [None for _ in range(5)]
        )
        self.bias1 = (
            torch.nn.ParameterList(
                [torch.nn.Parameter(torch.randn(5, 10)) for _ in range(5)]
            ).to(self.device)
            if has_bias
            else [None for _ in range(5)]
        )

    def forward(self, x):
        l1_out = torch.split(x.to(self.device), 10, dim=2)
        post_l1 = [
            torch.nn.functional.layer_norm(
                l1_out[i], (10,), weight=self.scale0[i], bias=self.bias0[i]
            )
            for i in range(len(l1_out))
        ]
        l1_out = torch.cat(post_l1, dim=2)

        l2_out = torch.split(l1_out, 10, dim=2)
        post_l2 = [
            torch.nn.functional.layer_norm(
                l2_out[i], (5, 10), weight=self.scale1[i], bias=self.bias1[i]
            )
            for i in range(len(l2_out))
        ]

        return torch.cat(post_l2, dim=2)


class MyModule4(torch.nn.Module):
    def __init__(self, z, device, has_bias):
        super().__init__()
        self.z = z
        self.device = device
        self.has_bias = has_bias
        self.seq_len = 10
        self.weights1 = [
            torch.nn.Parameter(torch.randn(z - i % 5, z)).to(self.device)
            for i in range(self.seq_len)
        ]
        self.weights2 = [
            torch.nn.Parameter(torch.randn(z - i % 5, z)).to(self.device)
            for i in range(self.seq_len)
        ]

        if has_bias:
            self.biases1 = [
                torch.nn.Parameter(torch.randn(z - i % 5)).to(self.device)
                for i in range(self.seq_len)
            ]
            self.biases2 = [
                torch.nn.Parameter(torch.randn(z - i % 5)).to(self.device)
                for i in range(self.seq_len)
            ]

    def forward(self, x):
        x = x + 1.2
        x1 = [
            torch.nn.functional.linear(
                x, self.weights1[i], self.biases1[i] if self.has_bias else None
            )
            for i in range(self.seq_len)
        ]
        x2 = torch.cat(x1, dim=1)
        x3 = torch.split(x2, 10, dim=1)
        x4 = torch.cat(x3)
        x5 = [
            torch.nn.functional.linear(
                x4, self.weights2[i], self.biases2[i] if self.has_bias else None
            )
            for i in range(self.seq_len)
        ]
        x6 = torch.cat(x5, dim=1)
        return torch.sigmoid(x6)


class MyModule5(torch.nn.Module):
    def __init__(self, device, has_bias=True):
        super().__init__()
        self.device = device

        self.weights = torch.nn.ParameterList(
            [torch.nn.Parameter(torch.randn(50, 100)).to(self.device) for _ in range(5)]
        )

        self.biases = (
            ([torch.nn.Parameter(torch.randn(50)).to(self.device) for _ in range(5)])
            if has_bias
            else [None for _ in range(5)]
        )

    def forward(self, x):
        l1_out = torch.split(x.to(self.device), 100, dim=1)
        l1_linear = [
            torch.nn.functional.linear(l1_out[i], self.weights[i], self.biases[i])
            for i in range(len(l1_out))
        ]
        l1_out = torch.cat(l1_linear, dim=1)
        return torch.sin(l1_out)


class TestPoitwiseOps(torch.nn.Module):
    def __init__(self, device, has_bias=True):
        super().__init__()
        self.device = device

    def forward(self, x):
        inputs = torch.split(x.to(self.device), 500, dim=1)
        x_split = torch.split(inputs[0].to(self.device), 50, dim=1)
        y_split = torch.split(inputs[1].to(self.device), 50, dim=1)
        tanh_1 = [torch.tanh(x_split[i]) for i in range(len(x_split))]
        tanh_2 = [torch.tanh(y_split[i]) for i in range(len(y_split))]
        sigmoid_1 = [torch.sigmoid(tanh_1[i]) for i in range(len(tanh_1))]
        sigmoid_2 = [torch.sigmoid(tanh_2[i]) for i in range(len(tanh_2))]
        relu_1 = [torch.nn.functional.relu(sigmoid_1[i]) for i in range(len(sigmoid_1))]
        relu_2 = [torch.nn.functional.relu(sigmoid_2[i]) for i in range(len(sigmoid_2))]
        add = [torch.add(relu_1[i], relu_2[i]) for i in range(len(relu_1))]
        mul = [torch.mul(add[i], add[i]) for i in range(len(add))]
        sub = [torch.sub(mul[i], mul[i]) for i in range(len(mul))]
        div = [torch.div(sub[i], sub[i]) for i in range(len(sub))]
        return torch.cat(div, dim=1)


@requires_cuda
@torch._inductor.config.patch(
    pre_grad_fusion_options={
        "batch_linear": {},
        "batch_linear_lhs": {},
        "batch_layernorm": {},
        "batch_tanh": {},
        "batch_relu": {},
        "batch_sigmoid": {},
    },
    post_grad_fusion_options={
        "batch_aten_add": {},
        "batch_aten_mul": {},
        "batch_aten_sub": {},
        "batch_aten_div": {},
        "group_linear": {"require_fbgemm": True},
    },
)
class TestGroupBatchFusion(TestCase):
    def compare_dict_tensors(self, ref_dict, res_dict, rtol=1e-3, atol=1e-3):
        if len(set(ref_dict.keys())) != len(set(res_dict.keys())):
            return False
        for key1 in ref_dict.keys():
            key2 = "_orig_mod." + key1
            assert key2 in res_dict, f"{key1} does not exist in traced module"
            if not torch.allclose(ref_dict[key1], res_dict[key2], rtol=rtol, atol=atol):
                return False
        return True

    def compare_pred(self, module, traced, input, rtol=1e-3, atol=1e-3):
        ref = module(*input)
        res = traced(*input)
        self.assertEqual(ref, res, rtol=rtol, atol=atol)

    def compare_parameters(self, module, traced, rtol=1e-3, atol=1e-3):
        ref_params = dict(module.named_parameters())
        res_params = dict(traced.named_parameters())
        self.assertTrue(self.compare_dict_tensors(ref_params, res_params, rtol, atol))

    def compare_gradients(self, module, traced, rtol=1e-3, atol=1e-3):
        ref_grad = {key: param.grad for key, param in module.named_parameters()}
        res_grad = {key: param.grad for key, param in traced.named_parameters()}
        self.assertTrue(
            self.compare_dict_tensors(ref_grad, res_grad, rtol=rtol, atol=atol)
        )

    @unittest.skipIf(not has_fbgemm, "requires fbgemm")
    def test_group_linear_fusion(self):
        z = 10
        for has_bias in [True, False]:
            counters.clear()
            module = MyModule(z, has_bias).to("cuda")
            input = [torch.randn(z, z, device="cuda")]
            traced = torch.compile(module)
            ref = module(*input)
            res = traced(*input)
            self.compare_pred(module, traced, input)
            self.assertEqual(
                counters["inductor"]["group_fusion"],
                2,
            )
            self.assertEqual(
                counters["inductor"]["batch_fusion"],
                0,
            )
            ref.sum().backward()
            res.sum().backward()
            self.compare_parameters(module, traced)
            self.compare_gradients(module, traced)
            self.assertEqual(
                counters["inductor"]["group_fusion"],
                4,
            )
            self.assertEqual(
                counters["inductor"]["batch_fusion"],
                3,
            )
            counters.clear()

    @unittest.skipIf(not has_fbgemm, "requires fbgemm")
    def test_group_linear_fusion_different_shapes(self):
        counters.clear()
        module = MyModule2().eval().to("cuda")
        input = [torch.rand(4, 24, device="cuda")]
        traced = torch.compile(module)
        ref = module(*input)
        res = traced(*input)
        self.compare_pred(module, traced, input)
        self.assertEqual(
            counters["inductor"]["group_fusion"],
            1,
        )
        self.assertEqual(
            counters["inductor"]["batch_fusion"],
            0,
        )
        ref.sum().backward()
        res.sum().backward()
        self.compare_parameters(module, traced)
        self.compare_gradients(module, traced)
        self.assertEqual(
            counters["inductor"]["group_fusion"],
            2,
        )
        self.assertEqual(
            counters["inductor"]["batch_fusion"],
            1,
        )
        counters.clear()

    def test_batch_layer_norm_fusion(self):
        for has_weight in [True, False]:
            for has_bias in [True, False]:
                counters.clear()
                module = MyModule3("cuda", has_weight, has_bias).to("cuda")
                input = [torch.randn(2, 5, 50, device="cuda")]
                traced = torch.compile(module)
                ref = module(*input)
                res = traced(*input)
                self.compare_pred(module, traced, input)
                self.assertEqual(
                    counters["inductor"]["group_fusion"],
                    0,
                )
                self.assertEqual(counters["inductor"]["batch_fusion"], 2)
                self.assertEqual(
                    counters["inductor"]["scmerge_split_removed"],
                    3,
                )
                self.assertEqual(
                    counters["inductor"]["scmerge_cat_removed"],
                    3,
                )
                ref.sum().backward()
                res.sum().backward()
                self.compare_parameters(module, traced, rtol=1e-8, atol=1e-8)
                self.compare_gradients(module, traced, rtol=1e-8, atol=1e-8)
                counters.clear()

    def test_batch_linear_lhs_fusion(self):
        z = 10
        for has_bias in [True, False]:
            counters.clear()
            module = MyModule4(z, "cuda", has_bias)
            input = [torch.randn(20, z, device="cuda")]
            traced = torch.compile(module)
            ref = module(*input)
            res = traced(*input)
            self.compare_pred(module, traced, input)
            self.assertEqual(counters["inductor"]["batch_fusion"], 2)
            self.assertEqual(
                counters["inductor"]["scmerge_split_removed"],
                1,
            )
            self.assertEqual(
                counters["inductor"]["scmerge_cat_removed"],
                1,
            )
            ref.sum().backward()
            res.sum().backward()
            self.compare_parameters(module, traced, rtol=1e-8, atol=1e-8)
            self.compare_gradients(module, traced, rtol=1e-8, atol=1e-8)
            counters.clear()

    def test_batch_linear_pre_grad_fusion(self):
        for has_bias in [True, False]:
            counters.clear()
            module = MyModule5("cuda", has_bias)
            input = [torch.randn(50, 500, device="cuda")]
            traced = torch.compile(module)
            ref = module(*input)
            res = traced(*input)
            self.compare_pred(module, traced, input)
            self.assertEqual(counters["inductor"]["batch_fusion"], 1)
            self.assertEqual(counters["inductor"]["group_fusion"], 0)
            self.assertEqual(
                counters["inductor"]["scmerge_split_removed"],
                2,
            )
            self.assertEqual(
                counters["inductor"]["scmerge_cat_removed"],
                2,
            )
            ref.sum().backward()
            res.sum().backward()
            self.compare_parameters(module, traced, rtol=1e-8, atol=1e-8)
            self.compare_gradients(module, traced, rtol=1e-8, atol=1e-8)
            counters.clear()

    def test_pointwise_op_fusion(self):
        counters.clear()
        module = TestPoitwiseOps("cuda")
        input = [torch.randn(50, 1000, requires_grad=True, device="cuda")]
        traced = torch.compile(module)
        ref = module(*input)
        res = traced(*input)
        self.compare_pred(module, traced, input)
        self.assertEqual(counters["inductor"]["batch_fusion"], 7)
        self.assertEqual(
            counters["inductor"]["scmerge_split_removed"],
            0,
        )
        self.assertEqual(
            counters["inductor"]["scmerge_cat_removed"],
            0,
        )
        ref.sum().backward()
        res.sum().backward()
        self.compare_parameters(module, traced, rtol=1e-8, atol=1e-8)
        self.compare_gradients(module, traced, rtol=1e-8, atol=1e-8)
        counters.clear()


class TestBMMFusionModule(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.my_modules = torch.nn.ModuleList()
        for _ in range(10):
            self.my_modules.append(torch.nn.Linear(10, 10))

    def forward(self, inputs):
        output = None
        for linear, input in zip(self.my_modules, inputs):
            if output is None:
                output = linear(input)
            else:
                output += linear(input)
        return output


@requires_cuda
@torch._inductor.config.patch(
    post_grad_fusion_options={"batch_linear_post_grad": {"require_fbgemm": False}}
)
class TestPostGradBatchLinearFusion(TestCase):
    def test_batch_linear_post_grad_fusion(self):
        pt1_module = TestBMMFusionModule().cuda()
        inputs = []
        for _ in range(10):
            inputs.append(torch.randn(10, 10).cuda())
        eager_output = pt1_module(inputs)
        pt2_module = torch.compile(pt1_module)
        pt2_output = pt2_module(inputs)
        self.assertTrue(torch.allclose(eager_output, pt2_output))
        self.assertEqual(
            counters["inductor"]["batch_fusion"],
            2,
        )


class TestFindIndependentSubsetGreedy(TestCase):
    # Helper function to build a Graph from a data description.
    def build_graph(self, desc):
        # desc: {
        #   "n1": ["n2", "n3"],
        #   "n2": ["n3"],
        #   "n3": [],
        # }
        #
        g = torch.fx.Graph()
        lookup = {}
        desc = collections.deque((k, v) for k, v in desc.items())
        unsatisfied = 0
        while desc:
            unsatisfied += 1
            assert unsatisfied <= len(desc)  # cycle or bad input?
            name, v = desc.popleft()
            args = tuple(lookup.get(n, None) for n in v)
            if None in args:
                desc.append((name, v))
                continue
            node = g.create_node("placeholder", "target", name=name, args=args)
            lookup[name] = node
            unsatisfied = 0
        return g, lookup

    def verify(self, tree, subnodes, min_fuse, max_fuse, expected):
        g, lookup = self.build_graph(tree)
        subnodes = [lookup[n] for n in subnodes]
        expected = [[lookup[n] for n in sub] for sub in expected]
        opts = {
            "min_fuse_set_size": min_fuse,
            "max_fuse_set_size": max_fuse,
        }
        result = list(
            torch._inductor.fx_passes.group_batch_fusion.find_independent_subset_greedy(
                subnodes, opts
            )
        )
        self.assertEqual(expected, result)

    def test_find_independent_subset_greedy(self):
        # First some randomly generated tests.
        self.verify({"n0": (), "n1": ()}, ["n0"], 0, 100, [["n0"]])
        self.verify(
            {"n0": (), "n1": (), "n2": ("n0",)}, ["n1", "n2"], 0, 100, [["n1", "n2"]]
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": ("n0",),
                "n3": (),
                "n4": ("n0", "n1", "n2"),
                "n5": ("n0", "n2", "n4"),
                "n6": ("n3",),
                "n7": ("n4", "n5", "n6", "n1", "n3"),
                "n8": ("n7", "n1", "n3", "n5", "n0"),
                "n9": ("n3", "n4", "n8", "n6", "n5", "n2", "n0", "n7"),
                "n10": ("n0",),
                "n11": ("n4", "n0", "n2", "n3", "n1", "n9"),
                "n12": ("n2", "n3", "n10", "n6", "n9"),
            },
            ["n10", "n5", "n3", "n4", "n9"],
            0,
            100,
            [["n10", "n5", "n3"], ["n4"], ["n9"]],
        )
        self.verify({"n0": (), "n1": (), "n2": ("n0",)}, ["n2"], 0, 100, [["n2"]])
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": (),
                "n4": ("n3", "n1", "n0"),
                "n5": ("n1", "n2", "n4", "n0"),
                "n6": ("n0", "n3", "n2"),
                "n7": ("n6", "n1", "n5", "n4", "n3", "n0"),
                "n8": ("n2", "n7", "n3"),
                "n9": ("n3", "n5", "n6", "n7", "n2", "n1"),
                "n10": ("n8", "n0", "n2", "n4", "n6", "n3"),
                "n11": ("n6", "n5", "n8", "n1", "n3", "n10", "n2"),
                "n12": ("n7", "n4"),
            },
            ["n7"],
            0,
            100,
            [["n7"]],
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": ("n1", "n2"),
                "n4": ("n1",),
                "n5": (),
                "n6": ("n5",),
                "n7": ("n1", "n6", "n5", "n2", "n3", "n0"),
                "n8": ("n5", "n7", "n2", "n6"),
                "n9": ("n1",),
                "n10": ("n9",),
                "n11": ("n3", "n4", "n0", "n2"),
                "n12": ("n8", "n9", "n5", "n1"),
                "n13": ("n11", "n4", "n12", "n1", "n9", "n3", "n0"),
            },
            ["n9", "n2", "n8", "n10", "n5", "n6", "n13", "n7", "n3", "n0", "n4"],
            0,
            100,
            [
                ["n9", "n2", "n5", "n0", "n4"],
                ["n8", "n10"],
                ["n6", "n3"],
                ["n13"],
                ["n7"],
            ],
        )
        self.verify({"n0": ()}, ["n0"], 0, 100, [["n0"]])
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": (),
                "n4": ("n1", "n2"),
                "n5": ("n0", "n4", "n1"),
                "n6": ("n1", "n5"),
                "n7": (),
                "n8": ("n7", "n1", "n3", "n5", "n6"),
                "n9": ("n2", "n1", "n8", "n0", "n4", "n7", "n6", "n5"),
                "n10": ("n4", "n7", "n2", "n3", "n8"),
                "n11": (),
                "n12": ("n9", "n7", "n5", "n11", "n8"),
                "n13": (
                    "n5",
                    "n6",
                    "n12",
                    "n3",
                    "n9",
                    "n8",
                    "n4",
                    "n11",
                    "n2",
                    "n10",
                    "n1",
                ),
                "n14": ("n7", "n3", "n12", "n10", "n2", "n0", "n4", "n5"),
                "n15": ("n9", "n5", "n1", "n13", "n8", "n10", "n12", "n7", "n11", "n3"),
                "n16": (
                    "n2",
                    "n4",
                    "n15",
                    "n5",
                    "n0",
                    "n6",
                    "n3",
                    "n8",
                    "n14",
                    "n12",
                    "n9",
                    "n10",
                    "n7",
                    "n13",
                ),
            },
            ["n0", "n3", "n2", "n11", "n1", "n6", "n12", "n5", "n4", "n15", "n8"],
            0,
            100,
            [
                ["n0", "n3", "n2", "n11", "n1"],
                ["n6"],
                ["n12"],
                ["n5"],
                ["n4"],
                ["n15"],
                ["n8"],
            ],
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": ("n2", "n1"),
                "n4": ("n2", "n3", "n1"),
                "n5": ("n3", "n1"),
                "n6": ("n1",),
                "n7": ("n5", "n4"),
                "n8": ("n6", "n2"),
            },
            ["n4", "n3", "n1", "n8", "n5", "n6", "n2"],
            0,
            100,
            [["n4", "n8", "n5"], ["n3", "n6"], ["n1", "n2"]],
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": ("n1", "n0"),
                "n4": ("n0",),
                "n5": ("n1", "n4"),
                "n6": ("n2", "n1", "n4"),
                "n7": ("n0", "n3"),
                "n8": ("n5", "n0", "n6", "n1", "n4", "n2", "n3"),
                "n9": ("n1", "n4", "n8", "n7", "n5"),
                "n10": ("n9", "n8", "n0", "n2", "n7", "n1", "n3", "n5"),
                "n11": ("n9", "n2", "n6", "n0", "n3"),
                "n12": ("n1", "n4", "n7", "n10", "n5", "n2", "n11", "n6"),
                "n13": ("n9", "n2", "n3", "n0", "n7", "n5", "n10", "n11"),
                "n14": (
                    "n8",
                    "n0",
                    "n3",
                    "n6",
                    "n10",
                    "n1",
                    "n5",
                    "n9",
                    "n12",
                    "n11",
                    "n4",
                ),
                "n15": (
                    "n3",
                    "n10",
                    "n0",
                    "n4",
                    "n9",
                    "n11",
                    "n2",
                    "n13",
                    "n12",
                    "n8",
                    "n5",
                    "n14",
                ),
                "n16": ("n6",),
                "n17": (
                    "n4",
                    "n3",
                    "n14",
                    "n8",
                    "n15",
                    "n16",
                    "n2",
                    "n5",
                    "n7",
                    "n12",
                    "n1",
                    "n0",
                    "n11",
                ),
            },
            ["n17", "n16", "n10", "n4", "n8", "n12", "n6", "n1"],
            0,
            100,
            [["n17"], ["n16", "n10"], ["n4", "n1"], ["n8"], ["n12"], ["n6"]],
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": ("n0",),
                "n3": ("n0", "n1"),
                "n4": ("n0",),
                "n5": ("n0",),
                "n6": ("n5", "n3", "n0", "n2"),
                "n7": (),
                "n8": ("n2", "n5", "n3", "n1", "n7", "n6", "n0"),
                "n9": ("n4",),
                "n10": ("n4", "n5", "n1", "n2", "n0", "n6", "n8", "n9", "n7"),
                "n11": ("n3", "n0", "n9", "n10", "n5", "n1", "n2", "n7", "n4", "n6"),
                "n12": ("n9", "n5"),
            },
            ["n8", "n3", "n1", "n12", "n2", "n5", "n11", "n4", "n10", "n6", "n0"],
            0,
            100,
            [
                ["n8", "n12"],
                ["n3", "n2", "n5", "n4"],
                ["n1", "n0"],
                ["n11"],
                ["n10"],
                ["n6"],
            ],
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": (),
                "n4": ("n2", "n3"),
                "n5": ("n1", "n3", "n2", "n4"),
                "n6": ("n5", "n4", "n1", "n3"),
                "n7": ("n5",),
                "n8": ("n5", "n4", "n1"),
                "n9": ("n2", "n3", "n1", "n5", "n7", "n0", "n8"),
                "n10": ("n5", "n3", "n1", "n7", "n8", "n9"),
                "n11": ("n1", "n4", "n2", "n0", "n8", "n9"),
                "n12": ("n4", "n3", "n9"),
                "n13": (
                    "n6",
                    "n10",
                    "n4",
                    "n8",
                    "n0",
                    "n11",
                    "n12",
                    "n7",
                    "n3",
                    "n2",
                    "n1",
                ),
                "n14": ("n4", "n13", "n2"),
                "n15": ("n11", "n7", "n6", "n10", "n14"),
                "n16": ("n15", "n3"),
                "n17": ("n10", "n2", "n7", "n0", "n5", "n6", "n9"),
                "n18": (
                    "n16",
                    "n8",
                    "n6",
                    "n9",
                    "n11",
                    "n12",
                    "n14",
                    "n5",
                    "n13",
                    "n4",
                    "n1",
                ),
            },
            [
                "n1",
                "n0",
                "n16",
                "n6",
                "n15",
                "n9",
                "n7",
                "n4",
                "n3",
                "n11",
                "n13",
                "n17",
                "n12",
                "n18",
            ],
            0,
            100,
            [
                ["n1", "n0", "n4"],
                ["n16", "n17"],
                ["n6", "n9"],
                ["n15"],
                ["n7"],
                ["n3"],
                ["n11", "n12"],
                ["n13"],
                ["n18"],
            ],
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": ("n2",),
                "n4": ("n1",),
                "n5": (),
                "n6": ("n1", "n4"),
                "n7": ("n5", "n1"),
                "n8": ("n6",),
                "n9": ("n6", "n1", "n2", "n0"),
                "n10": ("n0", "n7"),
                "n11": ("n0", "n4", "n3", "n5"),
                "n12": ("n9", "n8", "n7", "n4", "n0"),
            },
            ["n8", "n9", "n11", "n2", "n4", "n0", "n7", "n5", "n1"],
            0,
            100,
            [["n8", "n9", "n11", "n7"], ["n2", "n4", "n0", "n5"], ["n1"]],
        )
        self.verify(
            {"n0": (), "n1": (), "n2": (), "n3": ("n0",), "n4": ("n3",)},
            ["n1", "n2", "n4"],
            0,
            100,
            [["n1", "n2", "n4"]],
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": ("n1",),
                "n3": ("n2", "n1"),
                "n4": ("n3",),
                "n5": (),
                "n6": ("n1", "n5"),
                "n7": (),
                "n8": ("n4", "n5"),
                "n9": ("n0", "n3", "n6", "n4", "n5", "n8", "n7", "n1"),
                "n10": ("n3", "n0", "n6", "n9", "n7"),
                "n11": (),
                "n12": ("n1", "n8", "n3", "n6", "n7", "n0", "n10", "n5", "n9", "n11"),
                "n13": ("n9", "n11", "n4"),
                "n14": (),
                "n15": ("n6", "n12"),
                "n16": (
                    "n1",
                    "n7",
                    "n10",
                    "n3",
                    "n9",
                    "n0",
                    "n2",
                    "n5",
                    "n8",
                    "n13",
                    "n14",
                    "n15",
                    "n4",
                    "n6",
                ),
            },
            [
                "n11",
                "n16",
                "n5",
                "n12",
                "n7",
                "n2",
                "n0",
                "n6",
                "n3",
                "n9",
                "n8",
                "n15",
                "n14",
                "n4",
                "n13",
                "n1",
            ],
            0,
            100,
            [
                ["n11", "n5", "n7", "n2", "n0", "n14"],
                ["n16"],
                ["n12", "n13"],
                ["n6", "n3"],
                ["n9"],
                ["n8"],
                ["n15"],
                ["n4"],
                ["n1"],
            ],
        )
        self.verify({"n0": (), "n1": ()}, ["n1"], 0, 100, [["n1"]])
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": ("n1",),
                "n3": (),
                "n4": ("n0", "n2", "n3"),
                "n5": ("n2", "n3"),
                "n6": ("n3",),
            },
            ["n6", "n2", "n3", "n1"],
            0,
            100,
            [["n6", "n2"], ["n3", "n1"]],
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": ("n2",),
                "n4": ("n0",),
                "n5": ("n1", "n2"),
                "n6": ("n2", "n3", "n1", "n0", "n5"),
                "n7": ("n6", "n2", "n0", "n4", "n5", "n1"),
                "n8": ("n4",),
                "n9": ("n4", "n6", "n7", "n1", "n2"),
            },
            ["n8", "n6", "n2", "n4", "n7", "n5", "n3", "n9"],
            0,
            100,
            [["n8", "n6"], ["n2", "n4"], ["n7"], ["n5", "n3"], ["n9"]],
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": ("n1", "n2"),
                "n4": ("n0",),
                "n5": ("n2", "n3", "n0", "n1"),
                "n6": ("n4", "n1"),
                "n7": ("n5",),
                "n8": ("n7", "n1", "n5", "n6", "n3", "n4", "n0"),
                "n9": ("n2", "n8"),
            },
            ["n1", "n7", "n4", "n2", "n0", "n8", "n3", "n5"],
            0,
            100,
            [["n1", "n4", "n2"], ["n7"], ["n0", "n3"], ["n8"], ["n5"]],
        )
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": ("n0",),
                "n3": ("n1",),
                "n4": ("n2", "n1"),
                "n5": (),
                "n6": ("n0",),
                "n7": ("n6", "n3", "n2", "n1", "n0"),
                "n8": ("n0", "n2"),
                "n9": ("n6", "n5", "n8", "n4", "n0"),
                "n10": ("n1", "n7", "n5", "n8", "n6", "n2", "n4", "n9"),
            },
            ["n0"],
            0,
            100,
            [["n0"]],
        )

        # trivial test of min_fuse
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": ("n1", "n2"),
                "n4": ("n1",),
                "n5": (),
                "n6": ("n5",),
                "n7": ("n1", "n6", "n5", "n2", "n3", "n0"),
                "n8": ("n5", "n7", "n2", "n6"),
                "n9": ("n1",),
                "n10": ("n9",),
                "n11": ("n3", "n4", "n0", "n2"),
                "n12": ("n8", "n9", "n5", "n1"),
                "n13": ("n11", "n4", "n12", "n1", "n9", "n3", "n0"),
            },
            ["n9", "n2", "n8", "n10", "n5", "n6", "n13", "n7", "n3", "n0", "n4"],
            2,
            10,
            [["n9", "n2", "n5", "n0", "n4"], ["n8", "n10"], ["n6", "n3"]],
        )

        # trivial test of max_fuse
        self.verify(
            {
                "n0": (),
                "n1": (),
                "n2": (),
                "n3": ("n1", "n2"),
                "n4": ("n1",),
                "n5": (),
                "n6": ("n5",),
                "n7": ("n1", "n6", "n5", "n2", "n3", "n0"),
                "n8": ("n5", "n7", "n2", "n6"),
                "n9": ("n1",),
                "n10": ("n9",),
                "n11": ("n3", "n4", "n0", "n2"),
                "n12": ("n8", "n9", "n5", "n1"),
                "n13": ("n11", "n4", "n12", "n1", "n9", "n3", "n0"),
            },
            ["n9", "n2", "n8", "n10", "n5", "n6", "n13", "n7", "n3", "n0", "n4"],
            0,
            3,
            [
                ["n9", "n2", "n5"],
                ["n8", "n10", "n4"],
                ["n6", "n3", "n0"],
                ["n13"],
                ["n7"],
            ],
        )

    def test_find_independent_subset_greedy_fuse(self):
        # ensure that fusing the sets during iteration results in the correct
        # iteration results. In the example graph after we merge n2 and n3,
        # n4 is no longer independent from n1.
        g, lookup = self.build_graph(
            {
                "n0": (),
                "n1": (),
                "n2": ("n0",),
                "n3": ("n1",),
                "n4": ("n2",),
                "n5": (),
            }
        )
        opts = {
            "min_fuse_set_size": 0,
            "max_fuse_set_size": 100,
        }
        subnodes = ["n2", "n3", "n4", "n0", "n1", "n5"]
        subnodes = [lookup[n] for n in subnodes]
        i = torch._inductor.fx_passes.group_batch_fusion.find_independent_subset_greedy(
            subnodes, opts
        )
        self.assertEqual(next(i), [lookup[n] for n in ["n2", "n3", "n5"]])

        # fuse n2 and n3 which makes n4 now dependant on n1.
        args = tuple(lookup[n] for n in ["n0", "n1"])
        fused = g.create_node("placeholder", "target", name="n2+n3", args=args)
        lookup["n2"].replace_all_uses_with(fused)
        g.erase_node(lookup["n2"])
        lookup["n3"].replace_all_uses_with(fused)
        g.erase_node(lookup["n3"])

        self.assertEqual(next(i), [lookup[n] for n in ["n4"]])
        self.assertEqual(next(i), [lookup[n] for n in ["n0", "n1"]])
        self.assertRaises(StopIteration, lambda: next(i))


if __name__ == "__main__":
    run_tests()
