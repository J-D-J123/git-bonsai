# git-bonsai

<p align="center">
  <img src="docs/demo.gif" width="256" alt="A grown bonsai" />
</p>

A living pixel-art bonsai for your GitHub profile. It isn't redrawn from a template: the tree is
saved in your repo and **grows a little every night** from that day's contributions. Every
contribution is a growth step that extends a shoot, sprouts a branch, or adds leaves, and where
each step goes is decided by **quantum randomness** from ANU's quantum random number generator.
No two trees are alike.

![Growth from 30 to 6,000 contributions](docs/growth.png)

## How it grows

| Your activity | What the tree does |
| --- | --- |
| Each contribution | One growth step: extend a shoot, sprout a branch, or add leaves |
| All-time history | Height, branches, trunk thickness (a limb is as thick as the shoots it feeds) |
| Days since your last contribution | Season: green → yellowing (7d) → autumn (21d) → bare winter (60d) |
| Current streak | Pink blossoms |
| Yesterday's count | Petals drifting down in the animation |

It follows real tree rules: a leader trunk grows upward in a gentle curve, main branches reach
outward and fork into twigs, nothing grows downward, and leaves sit at the tips. Shoots that would
leave the bonsai's dome or grow into another branch get **pruned** and turn into denser foliage.

## Add it to your profile

1. Copy [examples/profile-workflow.yml](examples/profile-workflow.yml) into your profile repo
   (the one named after your username) as `.github/workflows/bonsai.yml`.
2. Add to your `README.md`:
   ```html
   <img src="bonsai/bonsai.gif" width="256" alt="My bonsai" />
   ```
3. Run the workflow once from the Actions tab. The first run replays your whole history to grow
   the starting tree; after that it grows nightly.

### Quantum randomness (optional)

Get a free key at [quantumnumbers.anu.edu.au](https://quantumnumbers.anu.edu.au) (the bonsai uses
one request a night) and add it as a repository secret named `QRNG_API_KEY`. Without it, growth
falls back to the OS's random source. `bonsai/tree.json` logs which source and seed each night used.

| Input | Default | Meaning |
| --- | --- | --- |
| `login` | repo owner | Account whose contributions grow the tree |
| `output-dir` | `bonsai` | Folder for `bonsai.gif`, `bonsai.png`, `tree.json` |
| `token` | built-in token | Reads public contributions |
| `qrng-api-key` | none | ANU quantum RNG key |

To start a new tree, delete `bonsai/tree.json`. The bot commits only the GIF and the saved tree, so
only your own commits count toward the bonsai.

## Run it locally

```sh
pip install pillow numpy
python bonsai.py --demo 1500 --out demo                      # fake history, fresh tree
GITHUB_TOKEN=... python bonsai.py --login <you> --out bonsai  # your real tree
```

## License

MIT
